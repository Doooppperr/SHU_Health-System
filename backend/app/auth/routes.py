import base64
import hashlib
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from flask import current_app, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)
from sqlalchemy.exc import IntegrityError

from app.auth import auth_bp
from app.extensions import db
from app.models import NotificationOutbox, PasswordVerificationChallenge, User
from app.services.account_email import effective_account_email, synchronize_institution_email
from app.services.contact import is_valid_email, normalize_email
from app.services.password_challenges import (
    claim_password_challenge,
    consume_password_challenges,
    increment_user_security_epochs,
    reserve_password_challenge_attempt,
    revoke_account_security_artifacts,
)


CAPTCHA_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
HEALTH_ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_captcha_store = {}
_password_challenge_issue_locks = {}
_password_challenge_issue_locks_guard = threading.Lock()


@contextmanager
def _password_challenge_issue_guard(user_id):
    """Serialize issuance for SQLite's local, single-process runtime.

    Production openGauss uses a database row lock below. SQLite is only used
    by local/demo and tests, where a per-account process lock supplies the
    equivalent thread-level exclusion without taking a database-wide lock.
    """

    if db.engine.dialect.name != "sqlite":
        yield
        return
    with _password_challenge_issue_locks_guard:
        lock = _password_challenge_issue_locks.setdefault(
            int(user_id),
            threading.Lock(),
        )
    with lock:
        yield


def _purge_expired_captchas(now=None):
    now = now or time.time()
    expired_ids = [
        challenge_id
        for challenge_id, challenge in _captcha_store.items()
        if challenge["expires_at"] <= now
    ]
    for challenge_id in expired_ids:
        _captcha_store.pop(challenge_id, None)


def _generate_captcha_code(length=4):
    return "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(length))


def _random_between(min_value, max_value):
    return min_value + secrets.randbelow(max_value - min_value + 1)


def _build_captcha_image(code):
    width = 128
    height = 44
    line_colors = ["#9db4c7", "#b6c4d2", "#a5b8a0", "#d0b47f", "#c2a2a2"]
    text_colors = ["#1f4b5f", "#34543f", "#6a4a1c", "#573c6b"]

    lines = []
    for _ in range(7):
        color = secrets.choice(line_colors)
        lines.append(
            f'<line x1="{_random_between(0, width)}" y1="{_random_between(0, height)}" '
            f'x2="{_random_between(0, width)}" y2="{_random_between(0, height)}" '
            f'stroke="{color}" stroke-width="{_random_between(1, 2)}" opacity="0.75" />'
        )

    chars = []
    for index, char in enumerate(code):
        x = 20 + index * 26 + _random_between(-3, 3)
        y = 29 + _random_between(-3, 4)
        angle = _random_between(-15, 15)
        color = secrets.choice(text_colors)
        chars.append(
            f'<text x="{x}" y="{y}" transform="rotate({angle} {x} {y})" '
            f'fill="{color}" font-size="24" font-family="Consolas, Arial, sans-serif" '
            f'font-weight="700">{char}</text>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" rx="6" fill="#f3f7fb" />'
        + "".join(lines)
        + "".join(chars)
        + "</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _create_captcha_challenge():
    now = time.time()
    _purge_expired_captchas(now)

    code = _generate_captcha_code()
    challenge_id = uuid4().hex
    ttl_seconds = current_app.config.get("CAPTCHA_TTL_SECONDS", 300)
    _captcha_store[challenge_id] = {
        "answer": code,
        "expires_at": now + ttl_seconds,
    }

    return challenge_id, code, _build_captcha_image(code)


def _verify_captcha(challenge_id, answer):
    _purge_expired_captchas()
    challenge = _captcha_store.pop(challenge_id, None)
    if not challenge:
        return False
    return secrets.compare_digest(challenge["answer"], answer.strip().upper())


def _build_auth_payload(user, message):
    claims = {"role": user.role, "token_version": user.token_version}
    access_token = create_access_token(identity=str(user.id), additional_claims=claims)
    refresh_token = create_refresh_token(identity=str(user.id), additional_claims=claims)
    return {
        "message": message,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": user.to_dict(),
    }


def _new_health_id() -> str:
    for _ in range(20):
        candidate = "HID-" + "".join(secrets.choice(HEALTH_ID_ALPHABET) for _ in range(8))
        if User.query.filter_by(health_id=candidate).first() is None:
            return candidate
    raise RuntimeError("unable to allocate a unique health identity")


def _request_ip_hash() -> str:
    raw = f"{request.remote_addr or 'unknown'}:{current_app.config['JWT_SECRET_KEY']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _aware(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _password_code_response(challenge_id=None, code=None):
    payload = {
        "message": "如果账号与邮箱信息匹配，验证码将发送到绑定邮箱，请注意查收",
        "challenge_id": challenge_id or str(uuid4()),
        "expires_in": 600,
    }
    if current_app.config.get("TESTING") and code:
        payload["verification_code"] = code
    return payload, 200


def _create_password_challenge(user, purpose):
    user_id = int(user.id)
    with _password_challenge_issue_guard(user_id):
        if db.engine.dialect.name == "sqlite":
            user = db.session.get(User, user_id, populate_existing=True)
        else:
            user = db.session.execute(
                db.select(User)
                .where(User.id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
        if (
            user is None
            or not user.is_active
            or user.role not in {"user", "institution_admin"}
            or not effective_account_email(user)
        ):
            db.session.rollback()
            return None, None

        # Recalculate every limit after acquiring the account lock. This makes
        # the account-level five-per-hour limit authoritative. The IP count is
        # intentionally an abuse-throttling layer rather than a cross-account
        # security invariant, so it does not require a global lock table.
        now = datetime.now(timezone.utc)
        ip_hash = _request_ip_hash()
        recent = PasswordVerificationChallenge.query.filter_by(
            user_id=user.id,
            purpose=purpose,
            consumed_at=None,
        ).filter(
            PasswordVerificationChallenge.token_version_snapshot
            == user.token_version,
            PasswordVerificationChallenge.created_at
            >= now - timedelta(seconds=60),
            PasswordVerificationChallenge.expires_at > now,
        ).first()
        hourly_account = PasswordVerificationChallenge.query.filter_by(
            user_id=user.id,
        ).filter(
            PasswordVerificationChallenge.created_at
            >= now - timedelta(hours=1)
        ).count()
        hourly_ip = PasswordVerificationChallenge.query.filter_by(
            request_ip_hash=ip_hash,
        ).filter(
            PasswordVerificationChallenge.created_at
            >= now - timedelta(hours=1)
        ).count()
        if recent:
            db.session.commit()
            return recent, None
        if hourly_account >= 5 or hourly_ip >= 5:
            active = PasswordVerificationChallenge.query.filter_by(
                user_id=user.id,
                purpose=purpose,
                consumed_at=None,
                token_version_snapshot=user.token_version,
            ).filter(PasswordVerificationChallenge.expires_at > now).order_by(
                PasswordVerificationChallenge.created_at.desc()
            ).first()
            db.session.commit()
            return active, None

        PasswordVerificationChallenge.query.filter_by(
            user_id=user.id,
            purpose=purpose,
            consumed_at=None,
        ).update({"consumed_at": now}, synchronize_session=False)
        code = f"{secrets.randbelow(1_000_000):06d}"
        email = effective_account_email(user)
        challenge = PasswordVerificationChallenge(
            user_id=user.id,
            purpose=purpose,
            email_snapshot=email,
            request_ip_hash=ip_hash,
            token_version_snapshot=user.token_version,
            expires_at=now + timedelta(minutes=10),
        )
        challenge.set_code(code)
        db.session.add(challenge)
        db.session.flush()
        db.session.add(NotificationOutbox(
            event_type="password_verification_code",
            idempotency_key=f"password-code:{challenge.public_id}",
            recipient=email,
            payload={
                "challenge_id": challenge.public_id,
                "verification_code": code,
                "purpose": purpose,
                "username": user.username,
                "expires_minutes": 10,
            },
        ))
        db.session.commit()
        return challenge, code


def _verify_password_challenge(public_id, code, purpose, *, user=None):
    challenge = PasswordVerificationChallenge.query.filter_by(public_id=public_id, purpose=purpose).first()
    now = datetime.now(timezone.utc)
    if (
        challenge is None
        or challenge.consumed_at is not None
        or _aware(challenge.expires_at) <= now
        or challenge.attempt_count >= 5
        or (user is not None and challenge.user_id != user.id)
        or challenge.token_version_snapshot != challenge.user.token_version
    ):
        return None, ({"message": "验证码无效或已过期，请重新获取", "code": "PASSWORD_CODE_INVALID"}, 400)
    if not reserve_password_challenge_attempt(
        challenge.id,
        user_id=challenge.user_id,
        token_version=challenge.user.token_version,
        attempted_at=now,
    ):
        db.session.rollback()
        return None, ({
            "message": "验证码无效或已过期，请重新获取",
            "code": "PASSWORD_CODE_INVALID",
        }, 400)
    if not challenge.check_code(str(code or "").strip()):
        db.session.commit()
        return None, ({"message": "验证码不正确，请检查后重试", "code": "PASSWORD_CODE_INCORRECT"}, 400)
    if effective_account_email(challenge.user) != normalize_email(challenge.email_snapshot):
        challenge.consumed_at = now
        db.session.commit()
        return None, ({"message": "绑定邮箱已经变更，请重新获取验证码", "code": "PASSWORD_EMAIL_CHANGED"}, 409)
    return challenge, None


def _claim_verified_password_challenge(challenge, user, claimed_at):
    if claim_password_challenge(
        challenge.id,
        user_id=user.id,
        token_version=user.token_version,
        claimed_at=claimed_at,
    ):
        return None
    db.session.rollback()
    return {
        "message": "验证码无效或已过期，请重新获取",
        "code": "PASSWORD_CODE_INVALID",
    }, 400


@auth_bp.get("/captcha")
def captcha():
    challenge_id, code, image = _create_captcha_challenge()
    payload = {
        "captcha_id": challenge_id,
        "image": image,
    }
    if current_app.config.get("TESTING"):
        payload["captcha_answer"] = code
    return payload, 200


@auth_bp.post("/register")
def register():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    email = normalize_email(payload.get("email"))
    phone = (payload.get("phone") or "").strip() or None
    invite_code = (payload.get("invite_code") or "").strip()
    captcha_id = (payload.get("captcha_id") or "").strip()
    captcha_answer = (payload.get("captcha_answer") or "").strip()

    if invite_code:
        return {
            "message": "机构账号不再开放自助注册，请联系平台管理员创建分院账号",
            "code": "INSTITUTION_SELF_REGISTRATION_DISABLED",
        }, 410

    if not username or not password or not email or not captcha_id or not captcha_answer:
        return {"message": "用户名、邮箱、密码和图片验证码均为必填项"}, 400
    if not is_valid_email(email):
        return {"message": "请输入有效的邮箱地址"}, 400

    if len(password) < 6:
        return {"message": "密码至少需要6个字符", "code": "PASSWORD_TOO_SHORT"}, 400

    if not _verify_captcha(captcha_id, captcha_answer):
        return {"message": "验证码不正确，请重新输入", "code": "INVALID_CAPTCHA"}, 400

    if User.query.filter_by(username=username).first():
        return {"message": "该用户名已被使用", "code": "USERNAME_EXISTS"}, 409

    user = User(
        username=username,
        email=email,
        phone=phone,
        role="user",
        managed_institution_id=None,
        health_id=_new_health_id(),
    )
    user.set_password(password)
    try:
        db.session.add(user)
        db.session.flush()
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {"message": "注册信息冲突，请检查后重试"}, 409

    return _build_auth_payload(user, "注册成功"), 201


@auth_bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    captcha_id = (payload.get("captcha_id") or "").strip()
    captcha_answer = (payload.get("captcha_answer") or "").strip()

    if not username or not password or not captcha_id or not captcha_answer:
        return {"message": "请输入用户名、密码和验证码", "code": "LOGIN_FIELDS_REQUIRED"}, 400

    if not _verify_captcha(captcha_id, captcha_answer):
        return {"message": "验证码不正确，请重新输入", "code": "INVALID_CAPTCHA"}, 400

    user = User.query.filter_by(username=username).first()
    if user is None or not user.check_password(password):
        return {"message": "用户名或密码不正确", "code": "INVALID_CREDENTIALS"}, 401
    if not user.is_active:
        return {"message": "该账号已停用，请联系管理员", "code": "ACCOUNT_INACTIVE"}, 403
    if user.role == "institution_admin" and (
        user.managed_institution is None
        or not user.managed_institution.is_active
        or user.managed_institution.organization is None
        or not user.managed_institution.organization.is_active
    ):
        return {"message": "该账号所属分院已停用", "code": "INSTITUTION_INACTIVE"}, 403

    return _build_auth_payload(user, "登录成功"), 200


@auth_bp.post("/password-reset/code")
def request_password_reset_code():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    email = normalize_email(payload.get("email"))
    captcha_id = (payload.get("captcha_id") or "").strip()
    captcha_answer = (payload.get("captcha_answer") or "").strip()
    if not username or not email or not captcha_id or not captcha_answer:
        return {"message": "请输入用户名、绑定邮箱和图片验证码", "code": "RESET_FIELDS_REQUIRED"}, 400
    if not _verify_captcha(captcha_id, captcha_answer):
        return {"message": "图片验证码不正确，请重新输入", "code": "INVALID_CAPTCHA"}, 400
    user = User.query.filter_by(username=username).first()
    if (
        user is None or user.role not in {"user", "institution_admin"} or not user.is_active
        or effective_account_email(user) != email
    ):
        return _password_code_response()
    challenge, code = _create_password_challenge(user, "reset")
    return _password_code_response(challenge.public_id if challenge else None, code)


@auth_bp.post("/password-reset/confirm")
def confirm_password_reset():
    payload = request.get_json(silent=True) or {}
    new_password = payload.get("new_password") or ""
    if len(new_password) < 6:
        return {"message": "新密码至少需要6个字符", "code": "PASSWORD_TOO_SHORT"}, 400
    challenge, error = _verify_password_challenge(
        (payload.get("challenge_id") or "").strip(), payload.get("verification_code"), "reset"
    )
    if error:
        return error
    user = challenge.user
    if user.role not in {"user", "institution_admin"} or not user.is_active:
        return {"message": "账号当前不可使用，请联系管理员", "code": "ACCOUNT_UNAVAILABLE"}, 403
    changed_at = datetime.now(timezone.utc)
    claim_error = _claim_verified_password_challenge(
        challenge,
        user,
        changed_at,
    )
    if claim_error:
        return claim_error
    user.set_password(new_password)
    if user.role == "institution_admin":
        user.must_change_initial_password = False
    user.email_verified_at = changed_at
    increment_user_security_epochs(user.id)
    revoke_account_security_artifacts(user.id, revoked_at=changed_at)
    db.session.commit()
    return {"message": "密码已重置，请使用新密码登录"}, 200


@auth_bp.post("/password-change/code")
@jwt_required()
def request_password_change_code():
    user = db.session.get(User, int(get_jwt_identity()))
    if user is None or user.role not in {"user", "institution_admin"} or not user.is_active:
        return {"message": "账号当前不可使用", "code": "ACCOUNT_UNAVAILABLE"}, 403
    if not effective_account_email(user):
        return {"message": "当前账号尚未绑定邮箱，请先联系管理员完善账号资料"}, 409
    challenge, code = _create_password_challenge(user, "change")
    if challenge is None:
        return {"message": "验证码发送过于频繁，请稍后再试", "code": "PASSWORD_CODE_RATE_LIMITED"}, 429
    payload, status = _password_code_response(challenge.public_id, code)
    payload["message"] = "验证码已发送到绑定邮箱，请注意查收"
    return payload, status


@auth_bp.post("/password-change/confirm")
@jwt_required()
def confirm_password_change():
    user = db.session.get(User, int(get_jwt_identity()))
    payload = request.get_json(silent=True) or {}
    new_password = payload.get("new_password") or ""
    if (
        user is None
        or user.role not in {"user", "institution_admin"}
        or not user.is_active
    ):
        return {"message": "账号当前不可使用", "code": "ACCOUNT_UNAVAILABLE"}, 403
    if not user.check_password(payload.get("current_password") or ""):
        return {"message": "当前密码不正确", "code": "CURRENT_PASSWORD_INCORRECT"}, 400
    if len(new_password) < 6:
        return {"message": "新密码至少需要6个字符", "code": "PASSWORD_TOO_SHORT"}, 400
    challenge, error = _verify_password_challenge(
        (payload.get("challenge_id") or "").strip(), payload.get("verification_code"), "change", user=user
    )
    if error:
        return error
    changed_at = datetime.now(timezone.utc)
    claim_error = _claim_verified_password_challenge(
        challenge,
        user,
        changed_at,
    )
    if claim_error:
        return claim_error
    user.set_password(new_password)
    if user.role == "institution_admin":
        user.must_change_initial_password = False
    user.email_verified_at = changed_at
    increment_user_security_epochs(user.id)
    revoke_account_security_artifacts(user.id, revoked_at=changed_at)
    db.session.commit()
    return {"message": "密码修改成功，请重新登录"}, 200


@auth_bp.put("/email")
@jwt_required()
def change_account_email():
    user = db.session.get(User, int(get_jwt_identity()))
    if user is None or not user.is_active:
        return {"message": "账号当前不可使用", "code": "ACCOUNT_UNAVAILABLE"}, 403
    if user.role not in {"user", "institution_admin"}:
        return {"message": "系统管理员不能自助修改绑定邮箱", "code": "EMAIL_CHANGE_FORBIDDEN"}, 403

    payload = request.get_json(silent=True) or {}
    new_email = normalize_email(payload.get("email"))
    if not new_email or not is_valid_email(new_email):
        return {"message": "请输入有效的新邮箱地址", "code": "INVALID_EMAIL"}, 400
    old_email = effective_account_email(user)
    if new_email == old_email:
        return {"message": "新邮箱不能与当前绑定邮箱相同", "code": "EMAIL_UNCHANGED"}, 409
    if user.role == "institution_admin" and user.managed_institution is None:
        return {"message": "当前机构账号未绑定分院，请联系系统管理员", "code": "INSTITUTION_UNAVAILABLE"}, 409
    if not user.check_password(str(payload.get("current_password") or "")):
        return {
            "message": "当前密码不正确",
            "code": "CURRENT_PASSWORD_INCORRECT",
        }, 400
    challenge, error = _verify_password_challenge(
        (payload.get("challenge_id") or "").strip(),
        payload.get("verification_code"),
        "change",
        user=user,
    )
    if error:
        return error

    changed_at = datetime.now(timezone.utc)
    claim_error = _claim_verified_password_challenge(
        challenge,
        user,
        changed_at,
    )
    if claim_error:
        return claim_error
    change_id = uuid4().hex
    if user.role == "institution_admin":
        institution = user.managed_institution
        synchronize_institution_email(institution, new_email)
        account_label = (
            f"{institution.organization.name}·{institution.branch_name}"
            if institution.organization else institution.branch_name
        )
    else:
        user.email = new_email
        user.email_verified_at = None
        account_label = user.username
    common_payload = {
        "username": user.username,
        "account_label": account_label,
        "old_email": old_email,
        "new_email": new_email,
        "changed_at": changed_at.isoformat(),
    }
    if old_email:
        db.session.add(NotificationOutbox(
            event_type="account_email_changed_old",
            idempotency_key=f"email-change:{change_id}:old",
            recipient=old_email,
            payload=common_payload,
        ))
    db.session.add(NotificationOutbox(
        event_type="account_email_changed_new",
        idempotency_key=f"email-change:{change_id}:new",
        recipient=new_email,
        payload=common_payload,
    ))
    consume_password_challenges(user.id, consumed_at=changed_at)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {"message": "邮箱修改冲突，请稍后重试", "code": "EMAIL_CHANGE_CONFLICT"}, 409
    return {"message": "绑定邮箱已修改，通知邮件正在发送", "user": user.to_dict()}, 200


@auth_bp.post("/refresh")
@jwt_required(refresh=True)
def refresh_token():
    claims = get_jwt()
    if claims.get("delegated") is True:
        from app.models import DelegationSessionAudit
        from app.services.delegation import issue_delegated_tokens

        audit = db.session.get(
            DelegationSessionAudit,
            claims.get("delegation_session_id"),
        )
        if audit is None or audit.status != "active":
            return {
                "message": "关联账号登录已失效，请重新登录",
                "code": "DELEGATION_SESSION_REVOKED",
            }, 401
        tokens = issue_delegated_tokens(audit)
        return {"access_token": tokens["access_token"]}, 200

    user_id = get_jwt_identity()
    # The JWT verification callback may already have placed this user in the
    # session identity map. Reload from the database so a password/account
    # mutation committed between verification and this handler cannot let an
    # old refresh token mint an access token in the new security epoch.
    user = db.session.get(User, int(user_id), populate_existing=True)
    if user is None:
        return {"message": "账号不存在或已不可用", "code": "USER_NOT_FOUND"}, 404
    if not user.is_active:
        return {"message": "该账号已停用，请联系管理员", "code": "ACCOUNT_INACTIVE"}, 403
    if claims.get("token_version", 0) != user.token_version:
        return {
            "message": "登录状态已经失效，请重新登录",
            "code": "TOKEN_REVOKED",
        }, 401

    access_token = create_access_token(identity=str(user.id), additional_claims={"role": user.role, "token_version": user.token_version})
    return {"access_token": access_token}, 200


@auth_bp.post("/logout")
@jwt_required()
def logout():
    claims = get_jwt()
    if claims.get("delegated") is True:
        from app.services.delegation import revoke_actor_login

        revoke_actor_login(int(claims["actor_id"]), "user logged out")
        db.session.commit()
        return {
            "message": "已退出关联账号登录，请重新登录",
            "redirect_to": "/login",
        }, 200
    from app.services.delegation import revoke_actor_login

    revoke_actor_login(int(get_jwt_identity()))
    db.session.commit()
    return {"message": "已安全退出登录"}, 200


@auth_bp.post("/delegation/exit")
@jwt_required()
def exit_delegated_session():
    claims = get_jwt()
    if claims.get("delegated") is not True:
        return {
            "message": "当前不是关联账号登录状态",
            "code": "DELEGATION_NOT_ACTIVE",
        }, 409
    from app.services.delegation import exit_delegation

    result, error = exit_delegation(claims)
    if error:
        return error
    db.session.commit()
    return result, 200


@auth_bp.post("/delegation/back")
@jwt_required()
def return_to_previous_delegated_account():
    claims = get_jwt()
    if claims.get("delegated") is not True:
        return {
            "message": "当前不是关联账号登录状态",
            "code": "DELEGATION_NOT_ACTIVE",
        }, 409
    from app.services.delegation import return_from_delegation

    result, error = return_from_delegation(claims)
    if error:
        return error
    db.session.commit()
    return result, 200
