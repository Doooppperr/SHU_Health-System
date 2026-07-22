from flask import request
from datetime import datetime, timezone
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
        return {"message": "请填写与亲友的关系"}, 400

    if len(relation_name) > 80:
        return {"message": "关系名称不能超过80个字符"}, 400

    target_user = None
    if friend_user_id is not None:
        target_user = db.session.get(User, friend_user_id)
    elif friend_username:
        target_user = User.query.filter_by(username=friend_username).first()
    else:
        return {"message": "请输入要添加的亲友用户名"}, 400

    if target_user is None:
        return {"message": "没有找到该普通用户，请检查用户名"}, 404

    if target_user.id == user_id:
        return {"message": "不能将自己的账号添加为亲友"}, 400

    existing = FriendRelation.query.filter_by(user_id=user_id, friend_user_id=target_user.id).first()
    if existing is not None:
        message = "该亲友已经添加并获得授权" if existing.auth_status else "该亲友已经添加，正在等待对方授权"
        return {"message": message}, 409

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
        return {"message": "只有亲友关系的添加者可以修改关系名称"}, 403

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
        return {"message": "只有被添加的亲友本人可以修改授权"}, 403

    payload = request.get_json(silent=True) or {}
    auth_status = _parse_bool(payload.get("auth_status"))
    if auth_status is None:
        return {"message": "授权状态不正确"}, 400

    relation.auth_status = auth_status
    db.session.commit()
    return {"item": relation.to_dict()}, 200


@friends_bp.put("/<int:relation_id>/booking-authorization")
@jwt_required()
def update_booking_authorization(relation_id: int):
    """The prospective examinee explicitly grants/revokes proxy-booking only."""
    user_id = _current_user_id()
    relation = _get_relation_visible_to_user(relation_id, user_id)
    if relation is None:
        return {"message": "friend relation not found"}, 404
    if relation.friend_user_id != user_id:
        return {"message": "只有被添加的亲友本人可以修改代预约授权"}, 403
    payload = request.get_json(silent=True) or {}
    allowed = _parse_bool(payload.get("booking_auth_status"))
    if allowed is None:
        return {"message": "代预约授权状态不正确"}, 400
    relation.booking_auth_status = allowed
    relation.booking_authorized_at = datetime.now(timezone.utc) if allowed else None
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
    return {"message": "亲友关系已删除"}, 200
