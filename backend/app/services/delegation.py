from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from flask_jwt_extended import create_access_token, create_refresh_token

from app.extensions import db
from app.models import DelegationSessionAudit, FriendRelation, User
from app.services.password_challenges import (
    increment_user_security_epochs,
    revoke_account_security_artifacts,
)


MAX_DELEGATION_DEPTH = 3
DELEGATED_ACCESS_TTL = timedelta(minutes=30)
DELEGATED_REFRESH_TTL = timedelta(hours=8)


def _aware(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def redacted_user(user):
    return {
        "id": user.id,
        "role": user.role,
        "display_name": user.real_name or "未完善姓名",
        "real_name": user.real_name or None,
        "identity_completed": user.profile_completed,
        "identity_completed_at": (
            user.identity_completed_at.isoformat()
            if user.identity_completed_at
            else None
        ),
        "profile_completed": user.profile_completed,
    }


def relation_for_users(first_user_id, second_user_id):
    return FriendRelation.query.filter(
        db.or_(
            db.and_(
                FriendRelation.user_id == first_user_id,
                FriendRelation.friend_user_id == second_user_id,
            ),
            db.and_(
                FriendRelation.user_id == second_user_id,
                FriendRelation.friend_user_id == first_user_id,
            ),
        )
    ).first()


def active_relation_for_users(first_user_id, second_user_id):
    relation = relation_for_users(first_user_id, second_user_id)
    return relation if relation is not None and relation.is_active else None


def active_counterpart_ids(user_id):
    rows = FriendRelation.query.filter(
        db.or_(
            FriendRelation.user_id == user_id,
            FriendRelation.friend_user_id == user_id,
        )
    ).all()
    return {
        relation.friend_user_id
        if relation.user_id == user_id
        else relation.user_id
        for relation in rows
        if relation.is_active
    }


def _claims_from_audit(audit):
    chain = [int(value) for value in (audit.chain_user_ids or [])]
    return {
        "role": "user",
        "token_version": int(
            (audit.token_version_snapshot or {}).get(
                str(audit.subject_user_id),
                -1,
            )
        ),
        "delegated": True,
        "delegation_session_id": audit.id,
        "actor_id": audit.actor_user_id,
        "subject_id": audit.subject_user_id,
        "chain": chain,
        "relation_chain": audit.relation_chain or [],
        "chain_token_versions": audit.token_version_snapshot or {},
        "delegation_depth": audit.depth,
    }


def issue_delegated_tokens(audit):
    claims = _claims_from_audit(audit)
    return {
        "access_token": create_access_token(
            identity=str(audit.subject_user_id),
            additional_claims=claims,
            expires_delta=DELEGATED_ACCESS_TTL,
        ),
        "refresh_token": create_refresh_token(
            identity=str(audit.subject_user_id),
            additional_claims=claims,
            expires_delta=DELEGATED_REFRESH_TTL,
        ),
    }


def issue_normal_tokens(user):
    claims = {
        "role": user.role,
        "token_version": user.token_version,
    }
    return {
        "access_token": create_access_token(
            identity=str(user.id),
            additional_claims=claims,
        ),
        "refresh_token": create_refresh_token(
            identity=str(user.id),
            additional_claims=claims,
        ),
    }


def start_delegation(current_user, relation, current_claims=None):
    current_claims = current_claims or {}
    current_user = db.session.get(
        User,
        current_user.id,
        populate_existing=True,
    )
    if current_user is None or not current_user.is_active or current_user.role != "user":
        return None, (
            {
                "message": "来源账号登录状态已失效，请重新登录",
                "code": "TOKEN_REVOKED",
            },
            401,
        )
    if current_claims.get("delegated") is True:
        if not validate_delegation_claims(current_claims):
            return None, (
                {
                    "message": "关联账号登录已失效，请重新登录",
                    "code": "DELEGATION_SESSION_REVOKED",
                },
                401,
            )
        actor_id = int(current_claims["actor_id"])
        chain = [int(value) for value in current_claims.get("chain") or []]
        relation_chain = list(current_claims.get("relation_chain") or [])
        parent_session_id = current_claims.get("delegation_session_id")
        source_versions = {
            str(int(user_id)): int(version)
            for user_id, version in (
                current_claims.get("chain_token_versions") or {}
            ).items()
        }
    else:
        actor_id = current_user.id
        chain = [current_user.id]
        relation_chain = []
        parent_session_id = None
        try:
            source_version = int(current_claims["token_version"])
        except (KeyError, TypeError, ValueError):
            source_version = -1
        if source_version != current_user.token_version:
            return None, (
                {
                    "message": "来源账号登录状态已失效，请重新登录",
                    "code": "TOKEN_REVOKED",
                },
                401,
            )
        source_versions = {str(current_user.id): source_version}

    if not chain or chain[-1] != current_user.id:
        return None, (
            {
                "message": "账号切换链路无效，请退出后重新切换",
                "code": "DELEGATION_CHAIN_INVALID",
            },
            409,
        )
    if set(source_versions) != {str(user_id) for user_id in chain}:
        return None, (
            {
                "message": "账号切换链路已失效",
                "code": "DELEGATION_CHAIN_INVALID",
            },
            409,
        )
    relation = db.session.get(
        FriendRelation,
        relation.id,
        populate_existing=True,
    )
    if relation is None or not relation.is_active:
        return None, (
            {
                "message": "亲友关联已失效，请重新发起并接受关联申请",
                "code": "RELATIONSHIP_INACTIVE",
            },
            409,
        )
    target = relation.counterparty_for(current_user.id)
    if target is None or not target.is_active or target.role != "user":
        return None, (
            {"message": "该亲友账号当前不可切换", "code": "DELEGATION_TARGET_UNAVAILABLE"},
            409,
        )
    if not relation.health_granted(current_user.id, target.id):
        return None, (
            {
                "message": "亲友关联已失效，请重新发起并接受关联申请",
                "code": "RELATIONSHIP_INACTIVE",
            },
            409,
        )
    if len(chain) - 1 >= MAX_DELEGATION_DEPTH:
        return None, (
            {
                "message": "亲友账号最多允许连续切换3层",
                "code": "DELEGATION_DEPTH_EXCEEDED",
            },
            409,
        )
    if target.id in chain:
        return None, (
            {"message": "不能在同一切换链路中重复进入账号", "code": "DELEGATION_CYCLE"},
            409,
        )

    chain.append(target.id)
    relation_chain.append(
        {
            "relation_id": relation.id,
            "viewer_id": current_user.id,
            "subject_id": target.id,
            "authorization_version": relation.authorization_version,
        }
    )
    users = (
        User.query.filter(User.id.in_(chain))
        .populate_existing()
        .all()
    )
    users_by_id = {user.id: user for user in users}
    if len(users_by_id) != len(chain):
        return None, (
            {"message": "账号切换链路已失效", "code": "DELEGATION_CHAIN_INVALID"},
            409,
        )
    for source_user_id in chain[:-1]:
        source_user = users_by_id[source_user_id]
        if (
            not source_user.is_active
            or source_user.role != "user"
            or source_versions.get(str(source_user_id))
            != source_user.token_version
        ):
            return None, (
                {
                    "message": "来源账号登录状态已失效，请重新登录",
                    "code": "TOKEN_REVOKED",
                },
                401,
            )
    target = users_by_id[target.id]
    if not target.is_active or target.role != "user":
        return None, (
            {
                "message": "该亲友账号当前不可切换",
                "code": "DELEGATION_TARGET_UNAVAILABLE",
            },
            409,
        )
    # Preserve every source epoch from the JWT instead of upgrading a stale
    # source to a concurrently changed database epoch. The target snapshot is
    # the value observed for this new hop; any later change invalidates it.
    versions = dict(source_versions)
    versions[str(target.id)] = target.token_version

    now = datetime.now(timezone.utc)
    audit = DelegationSessionAudit(
        id=str(uuid4()),
        actor_user_id=actor_id,
        subject_user_id=target.id,
        parent_session_id=parent_session_id,
        chain_user_ids=chain,
        relation_chain=relation_chain,
        token_version_snapshot=versions,
        depth=len(chain) - 1,
        status="active",
        created_at=now,
        expires_at=now + DELEGATED_REFRESH_TTL,
    )
    db.session.add(audit)
    db.session.flush()
    tokens = issue_delegated_tokens(audit)
    return {
        **tokens,
        "user": redacted_user(target),
        "session": {
            "delegated": True,
            "id": audit.id,
            "actor": {"id": actor_id},
            "subject": redacted_user(target),
            "chain": chain,
            "depth": audit.depth,
            "expires_at": audit.expires_at.isoformat(),
        },
    }, None


def validate_delegation_claims(payload):
    if payload.get("delegated") is not True:
        return True
    session_id = payload.get("delegation_session_id")
    audit = db.session.get(
        DelegationSessionAudit,
        session_id,
        populate_existing=True,
    )
    now = datetime.now(timezone.utc)
    if (
        audit is None
        or audit.status != "active"
        or _aware(audit.expires_at) <= now
        or str(payload.get("sub")) != str(audit.subject_user_id)
        or payload.get("actor_id") != audit.actor_user_id
        or payload.get("subject_id") != audit.subject_user_id
    ):
        if audit is not None and audit.status == "active" and _aware(audit.expires_at) <= now:
            audit.status = "expired"
            audit.ended_at = now
            audit.end_reason = "session expired"
            db.session.commit()
        return False

    chain = [int(value) for value in (payload.get("chain") or [])]
    stored_chain = [int(value) for value in (audit.chain_user_ids or [])]
    relation_chain = payload.get("relation_chain") or []
    if (
        chain != stored_chain
        or len(chain) != audit.depth + 1
        or not chain
        or chain[0] != audit.actor_user_id
        or chain[-1] != audit.subject_user_id
        or len(set(chain)) != len(chain)
        or relation_chain != (audit.relation_chain or [])
    ):
        return False

    snapshots = audit.token_version_snapshot or {}
    users = {
        row.id: row
        for row in (
            User.query.filter(User.id.in_(chain))
            .populate_existing()
            .all()
        )
    }
    for user_id in chain:
        user = users.get(user_id)
        if (
            user is None
            or not user.is_active
            or user.role != "user"
            or snapshots.get(str(user_id)) != user.token_version
        ):
            return False

    if len(relation_chain) != audit.depth:
        return False
    for index, item in enumerate(relation_chain):
        relation = db.session.get(
            FriendRelation,
            item.get("relation_id"),
            populate_existing=True,
        )
        if (
            relation is None
            or item.get("viewer_id") != chain[index]
            or item.get("subject_id") != chain[index + 1]
            or item.get("authorization_version") != relation.authorization_version
            or not relation.health_granted(chain[index], chain[index + 1])
        ):
            return False
    return True


def revoke_relation_sessions(relation_id, reason):
    now = datetime.now(timezone.utc)
    for audit in DelegationSessionAudit.query.filter_by(status="active").all():
        relation_ids = {
            item.get("relation_id")
            for item in (audit.relation_chain or [])
            if isinstance(item, dict)
        }
        if relation_id in relation_ids:
            audit.status = "revoked"
            audit.ended_at = now
            audit.end_reason = reason


def revoke_user_delegations(user_id, reason):
    """Revoke every active chain containing a deactivated account."""
    now = datetime.now(timezone.utc)
    revoked = 0
    target_id = int(user_id)
    for audit in DelegationSessionAudit.query.filter_by(status="active").all():
        chain = {int(value) for value in (audit.chain_user_ids or [])}
        if target_id not in chain:
            continue
        audit.status = "revoked"
        audit.ended_at = now
        audit.end_reason = reason
        revoked += 1
    return revoked


def close_delegation_chain(payload, reason="user logged out"):
    """End every active delegated session owned by the original actor.

    A user can create sibling branches and downstream sessions from the same
    login. Exiting any delegated branch is a full logout operation, so leaving
    even one sibling active would allow an already-issued JWT to keep working.
    """
    audit = db.session.get(
        DelegationSessionAudit,
        payload.get("delegation_session_id"),
    )
    if audit is None:
        return False
    revoke_actor_login(audit.actor_user_id, reason)
    return True


def close_actor_delegations(actor_user_id, reason="actor logged out"):
    now = datetime.now(timezone.utc)
    rows = DelegationSessionAudit.query.filter_by(
        actor_user_id=int(actor_user_id),
        status="active",
    ).all()
    for audit in rows:
        audit.status = "exited"
        audit.ended_at = now
        audit.end_reason = reason
    return len(rows)


def revoke_actor_login(actor_user_id, reason="actor logged out"):
    """Revoke the original login token version and every delegated branch."""
    actor = db.session.get(User, int(actor_user_id))
    if actor is None:
        return 0
    increment_user_security_epochs(actor.id)
    revoke_account_security_artifacts(actor.id)
    return close_actor_delegations(actor.id, reason)


def exit_delegation(payload):
    if not close_delegation_chain(payload, "user exited delegation"):
        return None, (
            {"message": "切换会话已失效，请重新登录", "code": "DELEGATION_EXIT_FAILED"},
            401,
        )
    return {
        "message": "已退出关联账号登录，请重新登录",
        "redirect_to": "/login",
    }, None
