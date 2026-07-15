from flask import request
from flask_jwt_extended import get_jwt_identity, jwt_required, verify_jwt_in_request

from app.extensions import db
from app.friends import friends_bp
from app.models import FriendRelation, User
from app.services.permissions import ROLE_USER, get_current_user, role_error


@friends_bp.before_request
def _require_regular_user_for_friends():
    verify_jwt_in_request()
    return role_error(get_current_user(), ROLE_USER)


def _current_user_id() -> int:
    return int(get_jwt_identity())


def _parse_optional_int(raw_value):
    if raw_value is None or raw_value == "":
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _parse_bool(raw_value):
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _get_relation_visible_to_user(relation_id: int, user_id: int):
    relation = db.session.get(FriendRelation, relation_id)
    if relation is None:
        return None

    if relation.user_id != user_id and relation.friend_user_id != user_id:
        return None

    return relation


@friends_bp.get("")
@jwt_required()
def list_friends():
    user_id = _current_user_id()
    outgoing = (
        FriendRelation.query.filter_by(user_id=user_id)
        .order_by(FriendRelation.created_at.desc(), FriendRelation.id.desc())
        .all()
    )
    incoming = (
        FriendRelation.query.filter_by(friend_user_id=user_id)
        .order_by(FriendRelation.created_at.desc(), FriendRelation.id.desc())
        .all()
    )

    return {
        "outgoing": [item.to_dict() for item in outgoing],
        "incoming": [item.to_dict() for item in incoming],
    }, 200


@friends_bp.post("")
@jwt_required()
def add_friend():
    user_id = _current_user_id()
    payload = request.get_json(silent=True) or {}

    friend_user_id = _parse_optional_int(payload.get("friend_user_id"))
    friend_username = (payload.get("friend_username") or "").strip()
    relation_name = (payload.get("relation_name") or "亲友").strip()

    if not relation_name:
        return {"message": "relation_name is required"}, 400

    if len(relation_name) > 80:
        return {"message": "relation_name must be <= 80 characters"}, 400

    target_user = None
    if friend_user_id is not None:
        target_user = db.session.get(User, friend_user_id)
    elif friend_username:
        target_user = User.query.filter_by(username=friend_username).first()
    else:
        return {"message": "friend_user_id or friend_username is required"}, 400

    if target_user is None:
        return {"message": "friend user not found"}, 404

    if target_user.id == user_id:
        return {"message": "cannot add yourself as friend"}, 400

    existing = FriendRelation.query.filter_by(user_id=user_id, friend_user_id=target_user.id).first()
    if existing is not None:
        return {"message": "friend relation already exists"}, 409

    relation = FriendRelation(
        user_id=user_id,
        friend_user_id=target_user.id,
        relation_name=relation_name,
        auth_status=False,
    )
    db.session.add(relation)
    db.session.commit()

    return {"item": relation.to_dict()}, 201


@friends_bp.put("/<int:relation_id>")
@jwt_required()
def rename_relation(relation_id: int):
    user_id = _current_user_id()
    relation = _get_relation_visible_to_user(relation_id, user_id)
    if relation is None:
        return {"message": "friend relation not found"}, 404

    if relation.user_id != user_id:
        return {"message": "only relation creator can rename"}, 403

    payload = request.get_json(silent=True) or {}
    relation_name = (payload.get("relation_name") or "").strip()
    if not relation_name:
        return {"message": "relation_name is required"}, 400

    if len(relation_name) > 80:
        return {"message": "relation_name must be <= 80 characters"}, 400

    relation.relation_name = relation_name
    db.session.commit()
    return {"item": relation.to_dict()}, 200


@friends_bp.put("/<int:relation_id>/authorization")
@jwt_required()
def update_authorization(relation_id: int):
    user_id = _current_user_id()
    relation = _get_relation_visible_to_user(relation_id, user_id)
    if relation is None:
        return {"message": "friend relation not found"}, 404

    if relation.friend_user_id != user_id:
        return {"message": "only friend user can update authorization"}, 403

    payload = request.get_json(silent=True) or {}
    auth_status = _parse_bool(payload.get("auth_status"))
    if auth_status is None:
        return {"message": "auth_status must be boolean"}, 400

    relation.auth_status = auth_status
    db.session.commit()
    return {"item": relation.to_dict()}, 200


@friends_bp.delete("/<int:relation_id>")
@jwt_required()
def delete_relation(relation_id: int):
    user_id = _current_user_id()
    relation = _get_relation_visible_to_user(relation_id, user_id)
    if relation is None:
        return {"message": "friend relation not found"}, 404

    db.session.delete(relation)
    db.session.commit()
    return {"message": "friend relation deleted"}, 200
