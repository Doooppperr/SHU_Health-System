from flask import current_app, request

from app.extensions import db
from app.models import (
    Comment, FriendRelation, InstitutionReport, ReportAsset,
    SelfMeasurement, User,
)
from app.services.permissions import ROLE_ADMIN, roles_required
from app.services.record_files import delete_report_urls
from app.services.storage import get_storage_backend
from app.users import users_bp


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
    return {"items": [item.to_dict(include_profile=False) for item in query.order_by(User.id).all()]}, 200


@users_bp.put("/<int:user_id>")
@roles_required(ROLE_ADMIN)
def update_user(user_id):
    from flask import g
    user = db.session.get(User, user_id)
    if not user:
        return {"message": "未找到该用户"}, 404
    payload = request.get_json(silent=True) or {}
    if "is_active" in payload:
        if user.id == g.current_user.id and payload["is_active"] is False:
            return {"message": "系统管理员不能停用自己的账号"}, 400
        user.is_active = bool(payload["is_active"])
    if "email" in payload:
        if user.role == "institution_admin":
            return {
                "message": "机构账号邮箱请由分院管理员在机构资料中统一修改",
                "code": "INSTITUTION_EMAIL_MANAGED_BY_BRANCH",
            }, 409
        user.email = (payload.get("email") or "").strip() or None
    if "phone" in payload:
        user.phone = (payload.get("phone") or "").strip() or None
    if "password" in payload:
        password = payload.get("password") or ""
        if len(password) < 6:
            return {"message": "密码长度不能少于 6 位"}, 400
        user.set_password(password)
    db.session.commit()
    return {"item": user.to_dict(include_profile=False)}, 200


@users_bp.delete("/<int:user_id>")
@roles_required(ROLE_ADMIN)
def delete_user(user_id):
    from flask import g
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
