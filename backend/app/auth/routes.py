import base64
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from flask import current_app, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt_identity,
    jwt_required,
)
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.auth import auth_bp
from app.extensions import db
from app.models import InstitutionInvite, NotificationOutbox, PasswordVerificationChallenge, User
from app.services.contact import is_valid_email, normalize_email


CAPTCHA_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
HEALTH_ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_captcha_store = {}


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


def _invite_code_hash(invite_code: str) -> str:
    normalized = invite_code.strip().upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
    now = datetime.now(timezone.utc)
    ip_hash = _request_ip_hash()
    recent = PasswordVerificationChallenge.query.filter_by(user_id=user.id, purpose=purpose).filter(
        PasswordVerificationChallenge.created_at >= now - timedelta(seconds=60)
    ).first()
    hourly_account = PasswordVerificationChallenge.query.filter_by(user_id=user.id).filter(
        PasswordVerificationChallenge.created_at >= now - timedelta(hours=1)
    ).count()
    hourly_ip = PasswordVerificationChallenge.query.filter_by(request_ip_hash=ip_hash).filter(
        PasswordVerificationChallenge.created_at >= now - timedelta(hours=1)
    ).count()
    if recent:
        return recent, None
    if hourly_account >= 5 or hourly_ip >= 5:
        active = PasswordVerificationChallenge.query.filter_by(
            user_id=user.id, purpose=purpose, consumed_at=None
        ).filter(PasswordVerificationChallenge.expires_at > now).order_by(
            PasswordVerificationChallenge.created_at.desc()
        ).first()
        return active, None

    PasswordVerificationChallenge.query.filter_by(
        user_id=user.id, purpose=purpose, consumed_at=None
    ).update({"consumed_at": now}, synchronize_session=False)
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = PasswordVerificationChallenge(
        user_id=user.id,
        purpose=purpose,
        email_snapshot=user.email,
        request_ip_hash=ip_hash,
        expires_at=now + timedelta(minutes=10),
    )
    challenge.set_code(code)
    db.session.add(challenge)
    db.session.flush()
    db.session.add(NotificationOutbox(
        event_type="password_verification_code",
        idempotency_key=f"password-code:{challenge.public_id}",
        recipient=user.email,
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
    ):
        return None, ({"message": "验证码无效或已过期，请重新获取", "code": "PASSWORD_CODE_INVALID"}, 400)
    if not challenge.check_code(str(code or "").strip()):
        challenge.attempt_count += 1
        db.session.commit()
        return None, ({"message": "验证码不正确，请检查后重试", "code": "PASSWORD_CODE_INCORRECT"}, 400)
    if normalize_email(challenge.user.email) != normalize_email(challenge.email_snapshot):
        challenge.consumed_at = now
        db.session.commit()
        return None, ({"message": "绑定邮箱已经变更，请重新获取验证码", "code": "PASSWORD_EMAIL_CHANGED"}, 409)
    return challenge, None


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

    invite = None
    expected_invite_hash = None
    if invite_code:
        invite = InstitutionInvite.query.filter_by(
            code_hash=_invite_code_hash(invite_code),
            status="active",
        ).first()
        if invite is None or invite.used_by_user_id is not None:
            return {"message": "邀请码不正确或已失效", "code": "INVITATION_UNAVAILABLE"}, 400
        if invite.institution is None or not invite.institution.is_active:
            return {"message": "该邀请码所属分院已停用", "code": "INSTITUTION_INACTIVE"}, 400
        expected_invite_hash = invite.code_hash

    user = User(
        username=username,
        email=email,
        phone=phone,
        role="institution_admin" if invite else "user",
        managed_institution_id=invite.institution_id if invite else None,
        health_id=None if invite else _new_health_id(),
    )
    user.set_password(password)
    try:
        db.session.add(user)
        db.session.flush()
        if invite is not None:
            consumed = db.session.execute(
                update(InstitutionInvite)
                .where(
                    InstitutionInvite.id == invite.id,
                    InstitutionInvite.code_hash == expected_invite_hash,
                    InstitutionInvite.status == "active",
                    InstitutionInvite.used_by_user_id.is_(None),
                )
                .values(
                    status="used",
                    used_by_user_id=user.id,
                    used_at=datetime.now(timezone.utc),
                )
                .execution_options(synchronize_session=False)
            )
            if consumed.rowcount != 1:
                db.session.rollback()
                return {"message": "该邀请码已经使用", "code": "INVITATION_USED"}, 409
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
        or normalize_email(user.email) != email
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
    user.set_password(new_password)
    user.token_version += 1
    user.email_verified_at = datetime.now(timezone.utc)
    challenge.consumed_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"message": "密码已重置，请使用新密码登录"}, 200


@auth_bp.post("/password-change/code")
@jwt_required()
def request_password_change_code():
    user = db.session.get(User, int(get_jwt_identity()))
    if user is None or user.role not in {"user", "institution_admin"} or not user.is_active:
        return {"message": "账号当前不可使用", "code": "ACCOUNT_UNAVAILABLE"}, 403
    if not user.email:
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
    if user is None or user.role not in {"user", "institution_admin"}:
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
    user.set_password(new_password)
    user.token_version += 1
    user.email_verified_at = datetime.now(timezone.utc)
    challenge.consumed_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"message": "密码修改成功，请重新登录"}, 200


@auth_bp.post("/refresh")
@jwt_required(refresh=True)
def refresh_token():
    user_id = get_jwt_identity()
    user = db.session.get(User, int(user_id))
    if user is None:
        return {"message": "账号不存在或已不可用", "code": "USER_NOT_FOUND"}, 404
    if not user.is_active:
        return {"message": "该账号已停用，请联系管理员", "code": "ACCOUNT_INACTIVE"}, 403

    access_token = create_access_token(identity=str(user.id), additional_claims={"role": user.role, "token_version": user.token_version})
    return {"access_token": access_token}, 200


@auth_bp.post("/logout")
@jwt_required()
def logout():
    return {"message": "已安全退出登录"}, 200
