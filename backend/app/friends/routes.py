from flask import g, request
from datetime import datetime, timezone
from flask_jwt_extended import (
    get_jwt,
    get_jwt_identity,
    jwt_required,
    verify_jwt_in_request,
)
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.friends import friends_bp
from app.models import FriendRelation, User
from app.services.delegation import (
    revoke_relation_sessions,
    start_delegation,
)
from app.services.permissions import ROLE_USER, get_current_user, role_error
from app.services.booking_participants import latest_intake_defaults
from app.services.user_access import profile_completion_error


@friends_bp.before_request
def _require_regular_user_for_friends():
    verify_jwt_in_request()
    user = get_current_user()
    error = role_error(user, ROLE_USER)
    if error:
        return error
    g.current_user = user
    return None


def _current_user_id() -> int:
    return int(get_jwt_identity())


def _friend_payload(relation, viewer_id):
    payload = relation.to_dict(viewer_id=viewer_id)
    if not payload.get("booking_granted_to_me"):
        return payload
    counterparty = relation.counterparty_for(viewer_id)
    values = latest_intake_defaults(counterparty.id)
    payload["recent_intake"] = {
        key: float(value)
        for key, value in values.items()
        if value is not None
    }
    return payload


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


def _require_completed_identity():
    return profile_completion_error(g.current_user)


def _relationship_state_conflict():
    db.session.rollback()
    return {
        "message": "亲友关联状态已经发生变化，请刷新后重试",
        "code": "RELATIONSHIP_STATE_CONFLICT",
    }, 409


def _activate_relation_cas(relation, user_id, when):
    """Accept one exact pending revision without reviving a concurrent revoke."""
    result = db.session.execute(
        update(FriendRelation)
        .where(
            FriendRelation.id == relation.id,
            FriendRelation.status == "pending",
            FriendRelation.friend_user_id == user_id,
            FriendRelation.authorization_version == relation.authorization_version,
            FriendRelation.booking_authorization_version
            == relation.booking_authorization_version,
        )
        .values(
            status="active",
            auth_status=True,
            reverse_auth_status=True,
            booking_auth_status=True,
            reverse_booking_auth_status=True,
            booking_authorized_at=when,
            reverse_booking_authorized_at=when,
            accepted_at=when,
            revoked_at=None,
            authorization_version=FriendRelation.authorization_version + 1,
            booking_authorization_version=(
                FriendRelation.booking_authorization_version + 1
            ),
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _revoke_relation_cas(relation, user_id, when):
    """Revoke one exact pending/active revision with an atomic version bump."""
    if relation.status not in {"pending", "active"}:
        return False
    result = db.session.execute(
        update(FriendRelation)
        .where(
            FriendRelation.id == relation.id,
            FriendRelation.status == relation.status,
            db.or_(
                FriendRelation.user_id == user_id,
                FriendRelation.friend_user_id == user_id,
            ),
            FriendRelation.authorization_version == relation.authorization_version,
            FriendRelation.booking_authorization_version
            == relation.booking_authorization_version,
        )
        .values(
            status="revoked",
            auth_status=False,
            reverse_auth_status=False,
            booking_auth_status=False,
            reverse_booking_auth_status=False,
            booking_authorized_at=None,
            reverse_booking_authorized_at=None,
            revoked_at=when,
            authorization_version=FriendRelation.authorization_version + 1,
            booking_authorization_version=(
                FriendRelation.booking_authorization_version + 1
            ),
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _reset_relation_pending_cas(
    relation,
    *,
    requester_id,
    target_id,
    relation_name,
    when,
):
    """Reapply from one exact revoked revision and invalidate stale writers."""
    result = db.session.execute(
        update(FriendRelation)
        .where(
            FriendRelation.id == relation.id,
            FriendRelation.status == "revoked",
            FriendRelation.authorization_version == relation.authorization_version,
            FriendRelation.booking_authorization_version
            == relation.booking_authorization_version,
        )
        .values(
            user_id=int(requester_id),
            friend_user_id=int(target_id),
            pair_key=FriendRelation.canonical_pair_key(requester_id, target_id),
            relation_name=relation_name,
            friend_relation_name=None,
            status="pending",
            auth_status=False,
            reverse_auth_status=False,
            booking_auth_status=False,
            reverse_booking_auth_status=False,
            booking_authorized_at=None,
            reverse_booking_authorized_at=None,
            accepted_at=None,
            revoked_at=None,
            created_at=when,
            authorization_version=FriendRelation.authorization_version + 1,
            booking_authorization_version=(
                FriendRelation.booking_authorization_version + 1
            ),
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _revoke_relation(relation, user_id, reason):
    if not _revoke_relation_cas(
        relation,
        user_id,
        datetime.now(timezone.utc),
    ):
        return _relationship_state_conflict()
    revoke_relation_sessions(relation.id, reason)
    db.session.commit()
    db.session.refresh(relation)
    return {
        "message": "亲友关联已撤销；如需再次关联，请重新发起申请",
        "item": {
            **relation.to_dict(viewer_id=user_id),
            "revoked_by_user_id": user_id,
        },
    }, 200


def _transition_relationship(relation, user_id, requested_active):
    if relation.status == "revoked":
        return {
            "message": "该亲友关联已撤销，请重新发起关联申请",
            "code": "FRIEND_RELATION_REAPPLY_REQUIRED",
        }, 409
    if relation.is_active:
        if requested_active:
            return {"item": relation.to_dict(viewer_id=user_id)}, 200
        return _revoke_relation(
            relation,
            user_id,
            "active friend relationship revoked",
        )

    if not requested_active:
        return _revoke_relation(
            relation,
            user_id,
            "pending friend request cancelled or rejected",
        )
    if relation.friend_user_id != user_id:
        return {
            "message": "只有收到申请的一方可以接受亲友关联",
            "code": "FRIEND_REQUEST_ACCEPT_FORBIDDEN",
        }, 403

    if not _activate_relation_cas(
        relation,
        user_id,
        datetime.now(timezone.utc),
    ):
        return _relationship_state_conflict()
    db.session.commit()
    db.session.refresh(relation)
    return {"item": relation.to_dict(viewer_id=user_id)}, 200


@friends_bp.get("")
@jwt_required()
def list_friends():
    user_id = _current_user_id()
    rows = (
        FriendRelation.query.filter(
            db.or_(
                FriendRelation.user_id == user_id,
                FriendRelation.friend_user_id == user_id,
            )
        )
        .order_by(FriendRelation.created_at.desc(), FriendRelation.id.desc())
        .all()
    )
    serialized = [(item, _friend_payload(item, user_id)) for item in rows]
    return {
        "items": [payload for _item, payload in serialized],
        # Compatibility keys for clients released before the bidirectional
        # relationship representation.
        "outgoing": [
            payload
            for item, payload in serialized
            if item.user_id == user_id
        ],
        "incoming": [
            payload
            for item, payload in serialized
            if item.friend_user_id == user_id
        ],
    }, 200


@friends_bp.post("")
@jwt_required()
def add_friend():
    error = _require_completed_identity()
    if error:
        return error
    user_id = _current_user_id()
    payload = request.get_json(silent=True) or {}

    health_id = (payload.get("health_id") or "").strip().upper()
    relation_name = (payload.get("relation_name") or "亲友").strip()

    if not relation_name:
        return {"message": "请填写与亲友的关系"}, 400

    if len(relation_name) > 80:
        return {"message": "关系名称不能超过80个字符"}, 400

    if not health_id:
        return {"message": "请输入亲友的健康身份码"}, 400
    target_user = User.query.filter_by(
        health_id=health_id,
        role="user",
        is_active=True,
    ).first()
    if target_user is None or not target_user.profile_completed:
        # Keep disabled, non-user, missing and incomplete targets
        # indistinguishable so this endpoint cannot be used as an account
        # directory. A valid target is only disclosed through the intended
        # pending relation with a masked display name.
        return {"message": "无法使用该健康身份码建立亲友关系，请核对后重试"}, 404

    if target_user.id == user_id:
        return {"message": "不能将自己的账号添加为亲友"}, 400

    existing = FriendRelation.query.filter(
        db.or_(
            db.and_(
                FriendRelation.user_id == user_id,
                FriendRelation.friend_user_id == target_user.id,
            ),
            db.and_(
                FriendRelation.user_id == target_user.id,
                FriendRelation.friend_user_id == user_id,
            ),
        )
    ).first()
    if existing is not None:
        if existing.status == "revoked":
            if not _reset_relation_pending_cas(
                existing,
                requester_id=user_id,
                target_id=target_user.id,
                relation_name=relation_name,
                when=datetime.now(timezone.utc),
            ):
                return _relationship_state_conflict()
            db.session.commit()
            db.session.refresh(existing)
            return {
                "item": existing.to_dict(viewer_id=user_id),
                "message": "亲友关联申请已重新发起",
            }, 201
        return {
            "message": "双方已经建立亲友关系，无需重复添加",
            "code": "FRIEND_RELATION_EXISTS",
            "item": existing.to_dict(viewer_id=user_id),
        }, 409

    relation = FriendRelation(
        user_id=user_id,
        friend_user_id=target_user.id,
        pair_key=FriendRelation.canonical_pair_key(user_id, target_user.id),
        relation_name=relation_name,
        auth_status=False,
    )
    db.session.add(relation)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {
            "message": "双方已经存在亲友关联，请刷新后重试",
            "code": "FRIEND_RELATION_EXISTS",
        }, 409

    return {"item": relation.to_dict(viewer_id=user_id)}, 201


@friends_bp.put("/<int:relation_id>")
@jwt_required()
def rename_relation(relation_id: int):
    error = _require_completed_identity()
    if error:
        return error
    user_id = _current_user_id()
    relation = _get_relation_visible_to_user(relation_id, user_id)
    if relation is None:
        return {"message": "friend relation not found"}, 404

    payload = request.get_json(silent=True) or {}
    relation_name = (
        payload.get("my_remark")
        if "my_remark" in payload
        else payload.get("relation_name")
    )
    relation_name = str(relation_name or "").strip()
    if not relation_name:
        return {"message": "relation_name is required"}, 400

    if len(relation_name) > 80:
        return {"message": "relation_name must be <= 80 characters"}, 400

    if relation.user_id == user_id:
        relation.relation_name = relation_name
    else:
        relation.friend_relation_name = relation_name
    db.session.commit()
    return {"item": relation.to_dict(viewer_id=user_id)}, 200


@friends_bp.put("/<int:relation_id>/authorization")
@jwt_required()
def update_authorization(relation_id: int):
    error = _require_completed_identity()
    if error:
        return error
    user_id = _current_user_id()
    relation = _get_relation_visible_to_user(relation_id, user_id)
    if relation is None:
        return {"message": "friend relation not found"}, 404

    payload = request.get_json(silent=True) or {}
    raw_status = (
        payload.get("health_view_granted_by_me")
        if "health_view_granted_by_me" in payload
        else payload.get("auth_status")
    )
    auth_status = _parse_bool(raw_status)
    if auth_status is None:
        return {"message": "授权状态不正确"}, 400
    return _transition_relationship(relation, user_id, auth_status)


@friends_bp.put("/<int:relation_id>/booking-authorization")
@jwt_required()
def update_booking_authorization(relation_id: int):
    """Compatibility endpoint mapped to the single relationship lifecycle."""
    error = _require_completed_identity()
    if error:
        return error
    user_id = _current_user_id()
    relation = _get_relation_visible_to_user(relation_id, user_id)
    if relation is None:
        return {"message": "friend relation not found"}, 404
    payload = request.get_json(silent=True) or {}
    raw_status = (
        payload.get("booking_granted_by_me")
        if "booking_granted_by_me" in payload
        else payload.get("booking_auth_status")
    )
    allowed = _parse_bool(raw_status)
    if allowed is None:
        return {"message": "代预约授权状态不正确"}, 400
    return _transition_relationship(relation, user_id, allowed)


@friends_bp.post("/<int:relation_id>/accept")
@jwt_required()
def accept_relation(relation_id: int):
    error = _require_completed_identity()
    if error:
        return error
    user_id = _current_user_id()
    relation = _get_relation_visible_to_user(relation_id, user_id)
    if relation is None:
        return {"message": "friend relation not found"}, 404
    return _transition_relationship(relation, user_id, True)


@friends_bp.post("/<int:relation_id>/switch-session")
@jwt_required()
def switch_session(relation_id: int):
    error = _require_completed_identity()
    if error:
        return error
    user_id = _current_user_id()
    relation = _get_relation_visible_to_user(relation_id, user_id)
    if relation is None:
        return {"message": "friend relation not found"}, 404
    result, error = start_delegation(g.current_user, relation, get_jwt())
    if error:
        return error
    db.session.commit()
    return result, 200


@friends_bp.delete("/<int:relation_id>")
@jwt_required()
def delete_relation(relation_id: int):
    error = _require_completed_identity()
    if error:
        return error
    user_id = _current_user_id()
    relation = _get_relation_visible_to_user(relation_id, user_id)
    if relation is None:
        return {"message": "friend relation not found"}, 404

    return _revoke_relation(relation, user_id, "friend relation deleted")
