from datetime import date, datetime, timezone

from flask import current_app, g, request

from app.extensions import db
from app.models import (
    BookingParticipantToken, Comment, FriendRelation, InstitutionReport,
    NotificationOutbox, ReportAsset, SelfMeasurement, User,
)
from app.services.delegation import revoke_user_delegations
from app.services.permissions import ROLE_ADMIN, roles_required
from app.services.record_files import delete_report_urls
from app.services.storage import get_storage_backend
from app.users import users_bp
from app.services.account_email import effective_account_email
from app.services.contact import is_valid_email
from app.services.password_challenges import (
    increment_user_security_epochs,
    revoke_account_security_artifacts,
)


def _admin_user_summary(user):
    item = user.to_dict(include_profile=False)
    if user.role == "user":
        item.update(
            real_name=user.real_name,
            birth_date=(
                user.birth_date.isoformat() if user.birth_date else None
            ),
            gender=user.gender,
            identity_completed=user.profile_completed,
            identity_completed_at=(
                user.identity_completed_at.isoformat()
                if user.identity_completed_at
                else None
            ),
            profile_completed=user.profile_completed,
            profile_completed_at=(
                user.identity_completed_at.isoformat()
                if user.identity_completed_at
                else None
            ),
        )
    return item


@users_bp.get("/me")
@roles_required("user", "institution_admin", "admin")
def get_me():
    from flask import g
    return {"user": g.current_user.to_dict()}, 200


@users_bp.get("")
@roles_required(ROLE_ADMIN)
def list_users():
    role = (request.args.get("role") or "").strip()
    query = User.query
    if role in {"user", "institution_admin", "admin"}:
        query = query.filter_by(role=role)
    active = (request.args.get("active") or "").strip().lower()
    if active in {"true", "1"}:
        query = query.filter_by(is_active=True)
    elif active in {"false", "0"}:
        query = query.filter_by(is_active=False)
    keyword = (request.args.get("q") or "").strip()
    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter(db.or_(
            User.username.ilike(pattern),
            User.real_name.ilike(pattern),
            User.email.ilike(pattern),
            User.phone.ilike(pattern),
            User.health_id.ilike(pattern),
        ))
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 20, type=int) or 20, 1), 100)
    total = query.count()
    rows = query.order_by(User.id).offset((page - 1) * size).limit(size).all()
    return {
        "items": [_admin_user_summary(item) for item in rows],
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200


@users_bp.get("/<int:user_id>")
@roles_required(ROLE_ADMIN)
def get_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return {"message": "未找到该用户"}, 404
    item = user.to_dict(include_profile=False)
    if user.role == "user":
        password_notice = NotificationOutbox.query.filter(
            NotificationOutbox.idempotency_key.like(f"admin-password:{user.id}:version:%"),
        ).order_by(NotificationOutbox.id.desc()).first()
        item.update({
            "real_name": user.real_name,
            "health_id": user.health_id,
            "birth_date": user.birth_date.isoformat() if user.birth_date else None,
            "gender": user.gender,
            "identity_completed": user.profile_completed,
            "identity_completed_at": (
                user.identity_completed_at.isoformat()
                if user.identity_completed_at
                else None
            ),
            "profile_completed": user.profile_completed,
            "profile_completed_at": (
                user.identity_completed_at.isoformat()
                if user.identity_completed_at
                else None
            ),
            "allow_health_id_proxy_booking": (
                user.allow_health_id_proxy_booking
            ),
            "health_id_booking_enabled": user.allow_health_id_proxy_booking,
            "password_notification": ({
                "outbox_id": password_notice.id,
                "status": password_notice.status,
                "attempts": password_notice.attempts,
                "sent_at": password_notice.sent_at.isoformat() if password_notice.sent_at else None,
            } if password_notice else None),
        })
    return {"item": item}, 200


@users_bp.post("/<int:user_id>/password")
@roles_required(ROLE_ADMIN)
def admin_change_password(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.role != "user":
        return {"message": "只能为普通用户修改密码"}, 404
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password") or "")
    if len(password) < 8 or len(password) > 128:
        return {"message": "新密码长度应为 8 至 128 位"}, 400
    email = effective_account_email(user)
    if not email or not is_valid_email(email):
        return {"message": "该用户没有有效通知邮箱，请先完善邮箱"}, 409
    user.set_password(password)
    increment_user_security_epochs(user.id)
    revoke_account_security_artifacts(user.id)
    key = f"admin-password:{user.id}:version:{user.token_version}"
    outbox = NotificationOutbox(
        event_type="admin_password_changed",
        idempotency_key=key,
        recipient=email,
        payload={
            "username": user.username,
            "account_label": user.real_name or user.username,
            "new_password": password,
            "changed_by": g.current_user.username,
            "login_url": "/login",
        },
    )
    db.session.add(outbox)
    db.session.commit()
    return {
        "message": "密码已修改，通知邮件已进入发送队列",
        "delivery": {"outbox_id": outbox.id, "status": outbox.status},
    }, 200


@users_bp.post("/<int:user_id>/password-notification/retry")
@roles_required(ROLE_ADMIN)
def retry_password_notification(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.role != "user":
        return {"message": "未找到该普通用户"}, 404
    outbox = NotificationOutbox.query.filter(
        NotificationOutbox.idempotency_key.like(f"admin-password:{user.id}:version:%"),
    ).order_by(NotificationOutbox.id.desc()).first()
    if outbox is None:
        return {"message": "没有可重试的密码通知"}, 404
    if outbox.status == "sent":
        return {"message": "密码通知已经发送成功"}, 409
    if not (outbox.payload or {}).get("new_password"):
        return {"message": "敏感密码内容已清除，不能再次发送"}, 409
    outbox.status = "pending"
    outbox.next_attempt_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"message": "通知邮件已重新进入发送队列"}, 200


@users_bp.put("/<int:user_id>")
@roles_required(ROLE_ADMIN)
def update_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return {"message": "未找到该用户"}, 404
    payload = request.get_json(silent=True) or {}
    if "is_active" in payload:
        desired_active = payload["is_active"]
        if not isinstance(desired_active, bool):
            return {"message": "is_active 必须为布尔值"}, 400
        if user.id == g.current_user.id and desired_active is False:
            return {"message": "系统管理员不能停用自己的账号"}, 400
        if desired_active != user.is_active:
            now = datetime.now(timezone.utc)
            user.is_active = desired_active
            # Both deactivation and restoration are security boundaries.  A
            # monotonic version bump ensures credentials that were invalid
            # during deactivation can never become valid again on restore.
            increment_user_security_epochs(
                user.id,
                booking_authorization_version=True,
            )
            revoke_account_security_artifacts(user.id, revoked_at=now)
            revoke_user_delegations(
                user.id,
                "account active state changed by administrator",
            )
            BookingParticipantToken.query.filter(
                db.or_(
                    BookingParticipantToken.booker_user_id == user.id,
                    BookingParticipantToken.subject_user_id == user.id,
                ),
                BookingParticipantToken.consumed_at.is_(None),
                BookingParticipantToken.revoked_at.is_(None),
            ).update({"revoked_at": now}, synchronize_session=False)
    if "email" in payload:
        if user.role == "institution_admin":
            return {
                "message": "机构账号邮箱请由分院管理员在机构资料中统一修改",
                "code": "INSTITUTION_EMAIL_MANAGED_BY_BRANCH",
            }, 409
        email = (payload.get("email") or "").strip() or None
        if email != user.email:
            changed_at = datetime.now(timezone.utc)
            user.email = email
            user.email_verified_at = None
            increment_user_security_epochs(user.id)
            revoke_account_security_artifacts(
                user.id,
                revoked_at=changed_at,
            )
    if "phone" in payload:
        user.phone = (payload.get("phone") or "").strip() or None
    if user.role == "user":
        if {"real_name", "birth_date", "gender"}.intersection(payload):
            return {
                "message": "请使用管理员实名认证修正接口修改姓名、出生日期或性别",
                "code": "BASIC_PROFILE_CORRECTION_REQUIRED",
            }, 409
    if "password" in payload:
        return {"message": "请使用管理员密码修改接口"}, 400
    db.session.commit()
    return {
        "item": _admin_user_summary(user),
    }, 200


@users_bp.delete("/<int:user_id>")
@roles_required(ROLE_ADMIN)
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return {"message": "未找到该用户"}, 404
    if user.id == g.current_user.id:
        return {"message": "系统管理员不能删除自己的账号"}, 400
    if user.role == "admin":
        return {"message": "不能在这里删除系统管理员账号"}, 400
    if user.role == "institution_admin":
        return {"message": "请通过机构账号删除功能处理机构管理员"}, 400
    if (request.get_json(silent=True) or {}).get("confirm") is not True:
        return {"message": "删除后无法恢复，请先进行删除确认"}, 400

    report_urls = [row.temporary_file_url for row in InstitutionReport.query.filter_by(matched_user_id=user.id).all()]
    asset_keys = [row.storage_key for row in db.session.query(ReportAsset).join(InstitutionReport).filter(InstitutionReport.matched_user_id == user.id).all()]
    InstitutionReport.query.filter_by(matched_user_id=user.id).delete(synchronize_session=False)
    SelfMeasurement.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    FriendRelation.query.filter(
        (FriendRelation.user_id == user.id) | (FriendRelation.friend_user_id == user.id)
    ).delete(synchronize_session=False)
    Comment.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    db.session.delete(user)
    db.session.commit()
    delete_report_urls(report_urls)
    storage = get_storage_backend(current_app.config)
    for key in asset_keys: storage.delete(key)
    return {"message": "用户及其关联业务数据已删除"}, 200
