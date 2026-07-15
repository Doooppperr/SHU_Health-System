from flask import request

from app.extensions import db
from app.models import (
    Comment, FriendRelation, InstitutionReport,
    SelfMeasurement, User,
)
from app.services.permissions import ROLE_ADMIN, roles_required
from app.services.record_files import delete_report_urls
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
        return {"message": "user not found"}, 404
    payload = request.get_json(silent=True) or {}
    if "is_active" in payload:
        if user.id == g.current_user.id and payload["is_active"] is False:
            return {"message": "admin cannot deactivate own account"}, 400
        user.is_active = bool(payload["is_active"])
    if "email" in payload:
        user.email = (payload.get("email") or "").strip() or None
    if "phone" in payload:
        user.phone = (payload.get("phone") or "").strip() or None
    if "password" in payload:
        password = payload.get("password") or ""
        if len(password) < 6:
            return {"message": "password must be at least 6 characters"}, 400
        user.set_password(password)
    db.session.commit()
    return {"item": user.to_dict(include_profile=False)}, 200


@users_bp.delete("/<int:user_id>")
@roles_required(ROLE_ADMIN)
def delete_user(user_id):
    from flask import g
    user = db.session.get(User, user_id)
    if not user:
        return {"message": "user not found"}, 404
    if user.id == g.current_user.id:
        return {"message": "admin cannot delete own account"}, 400
    if user.role == "admin":
        return {"message": "administrator accounts cannot be deleted here"}, 400
    if user.role == "institution_admin":
        return {"message": "use the institution account deletion endpoint"}, 400
    if (request.get_json(silent=True) or {}).get("confirm") is not True:
        return {"message": "irreversible deletion requires confirm=true"}, 400

    report_urls = [row.temporary_file_url for row in InstitutionReport.query.filter_by(matched_user_id=user.id).all()]
    InstitutionReport.query.filter_by(matched_user_id=user.id).delete(synchronize_session=False)
    SelfMeasurement.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    FriendRelation.query.filter(
        (FriendRelation.user_id == user.id) | (FriendRelation.friend_user_id == user.id)
    ).delete(synchronize_session=False)
    Comment.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    db.session.delete(user)
    db.session.commit()
    delete_report_urls(report_urls)
    return {"message": "user and all associated business data deleted"}, 200
