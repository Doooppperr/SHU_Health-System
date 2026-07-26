from datetime import date

from flask import g, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.profile import profile_bp
from app.services.permissions import ROLE_USER, roles_required
PROFILE_FIELDS = {"real_name", "birth_date", "gender", "allergy_history", "medical_history", "phone"}
GENDERS = {"male", "female", "other", "undisclosed"}


@profile_bp.get("/me")
@roles_required(ROLE_USER)
def get_profile():
    return {"item": g.current_user.to_dict()}, 200


@profile_bp.put("/me")
@roles_required(ROLE_USER)
def update_profile():
    payload = request.get_json(silent=True) or {}
    if "health_id" in payload and payload.get("health_id") != g.current_user.health_id:
        return {"message": "health_id is immutable"}, 409
    if not PROFILE_FIELDS.intersection(payload):
        return {"message": "no editable profile field supplied"}, 400
    if "real_name" in payload:
        g.current_user.real_name = (payload.get("real_name") or "").strip() or None
    if "birth_date" in payload:
        raw = payload.get("birth_date")
        try:
            parsed = date.fromisoformat(raw) if raw else None
        except (TypeError, ValueError):
            return {"message": "birth_date must be YYYY-MM-DD"}, 400
        if parsed and parsed > date.today():
            return {"message": "birth_date cannot be in the future"}, 400
        g.current_user.birth_date = parsed
    if "gender" in payload:
        gender = payload.get("gender") or None
        if gender not in GENDERS | {None}:
            return {"message": "invalid gender"}, 400
        g.current_user.gender = gender
    for field in ("allergy_history", "medical_history", "phone"):
        if field in payload:
            setattr(g.current_user, field, (payload.get(field) or "").strip() or None)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {"message": "资料保存冲突，请检查后重试"}, 409
    return {"item": g.current_user.to_dict()}, 200
