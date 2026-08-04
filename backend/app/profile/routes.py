from datetime import date, datetime, timezone

from flask import g, request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import User
from app.profile import profile_bp
from app.services.password_challenges import increment_user_security_epochs
from app.services.permissions import ROLE_USER, roles_required
PROFILE_FIELDS = {
    "allergy_history",
    "medical_history",
    "phone",
    "health_id_booking_enabled",
    "allow_health_id_proxy_booking",
}
IDENTITY_FIELDS = {"real_name", "birth_date", "gender"}
GENDERS = {"male", "female", "other", "undisclosed"}


def _complete_identity_cas(user_id, *, real_name, birth_date, gender, completed_at):
    result = db.session.execute(
        update(User)
        .where(
            User.id == user_id,
            User.identity_completed_at.is_(None),
        )
        .values(
            real_name=real_name,
            birth_date=birth_date,
            gender=gender,
            identity_completed_at=completed_at,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


@profile_bp.get("/me")
@roles_required(ROLE_USER)
def get_profile():
    return {"item": g.current_user.to_dict()}, 200


@profile_bp.post("/me/complete")
@roles_required(ROLE_USER)
def complete_profile():
    user = g.current_user
    if user.profile_completed:
        return {
            "message": "实名认证信息已确认，如需更正请联系平台管理员",
            "code": "PROFILE_ALREADY_COMPLETED",
        }, 409
    payload = request.get_json(silent=True) or {}
    if set(payload) - IDENTITY_FIELDS:
        return {
            "message": "实名认证仅接受姓名、出生日期和性别",
            "code": "PROFILE_COMPLETION_FIELDS_INVALID",
        }, 400
    real_name = str(payload.get("real_name") or "").strip()
    if not real_name or len(real_name) > 80:
        return {"message": "姓名不能为空且不能超过80个字符"}, 400
    try:
        birth_date = date.fromisoformat(str(payload.get("birth_date") or ""))
    except (TypeError, ValueError):
        return {"message": "出生日期格式应为 YYYY-MM-DD"}, 400
    if birth_date > date.today():
        return {"message": "出生日期不能晚于今天"}, 400
    gender = payload.get("gender")
    if gender not in GENDERS:
        return {"message": "请选择有效的性别"}, 400

    try:
        completed = _complete_identity_cas(
            user.id,
            real_name=real_name,
            birth_date=birth_date,
            gender=gender,
            completed_at=datetime.now(timezone.utc),
        )
        if not completed:
            db.session.rollback()
            return {
                "message": "实名认证信息已确认，如需更正请联系平台管理员",
                "code": "PROFILE_ALREADY_COMPLETED",
            }, 409
        db.session.commit()
        db.session.refresh(user)
    except IntegrityError:
        db.session.rollback()
        return {"message": "实名认证信息保存冲突，请稍后重试"}, 409
    return {"item": user.to_dict()}, 200


@profile_bp.put("/me")
@roles_required(ROLE_USER)
def update_profile():
    payload = request.get_json(silent=True) or {}
    if "health_id" in payload and payload.get("health_id") != g.current_user.health_id:
        return {"message": "health_id is immutable"}, 409
    if IDENTITY_FIELDS.intersection(payload):
        return {
            "message": "姓名、出生日期和性别只能通过实名认证一次性提交，修改请联系平台管理员",
            "code": "PROFILE_IDENTITY_IMMUTABLE",
        }, 409
    if not PROFILE_FIELDS.intersection(payload):
        return {"message": "no editable profile field supplied"}, 400
    for field in ("allergy_history", "medical_history", "phone"):
        if field in payload:
            setattr(g.current_user, field, (payload.get(field) or "").strip() or None)
    proxy_fields = {
        key: payload[key]
        for key in (
            "allow_health_id_proxy_booking",
            "health_id_booking_enabled",
        )
        if key in payload
    }
    proxy_values = list(proxy_fields.values())
    if len(proxy_values) > 1 and proxy_values[0] != proxy_values[1]:
        return {"message": "健康身份码代预约开关字段不一致"}, 400
    if proxy_fields:
        raw = next(iter(proxy_fields.values()))
        if not isinstance(raw, bool):
            return {"message": "健康身份码代预约开关必须是布尔值"}, 400
        if raw != g.current_user.allow_health_id_proxy_booking:
            g.current_user.allow_health_id_proxy_booking = raw
            increment_user_security_epochs(
                g.current_user.id,
                token_version=False,
                booking_authorization_version=True,
            )
            if raw is False:
                from app.services.booking_participants import (
                    revoke_health_id_booking_access,
                )

                revoke_health_id_booking_access(g.current_user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {"message": "资料保存冲突，请检查后重试"}, 409
    return {"item": g.current_user.to_dict()}, 200
