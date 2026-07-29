from __future__ import annotations

from datetime import datetime, timezone

from app.agent.crypto import decrypt_json
from app.extensions import db
from app.models import (
    AgentActionExecution,
    AgentPendingAction,
    SupportHandoff,
)


class ActionConflict(RuntimeError):
    pass


def _domain_result(response):
    payload, status = response
    if status >= 400:
        raise ActionConflict(
            str(payload.get("message") or payload.get("code") or "业务规则不允许执行")
        )
    return payload


def execute_approved_action(action: AgentPendingAction, user):
    existing = AgentActionExecution.query.filter_by(action_id=action.id).first()
    if existing and existing.status == "completed":
        return existing.result
    if existing and existing.status == "started":
        raise ActionConflict("该操作正在执行，请稍后查询结果")

    now = datetime.now(timezone.utc)
    if action.user_id != user.id:
        raise PermissionError("当前账号不能确认该操作")
    expires_at = action.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at <= now:
        action.status = "expired"
        db.session.commit()
        raise ActionConflict("操作草稿已经过期，请重新发起")
    if action.status not in {"pending", "approved"}:
        raise ActionConflict("操作草稿当前不能执行")

    execution = AgentActionExecution(
        action_id=action.id,
        idempotency_key=action.id,
        status="started",
    )
    db.session.add(execution)
    db.session.flush()
    payload = decrypt_json(action.encrypted_payload, purpose=f"agent-action:{action.id}")

    if action.action_type == "booking":
        from app.booking_v7.routes import create_booking_group_for_user

        domain = _domain_result(
            create_booking_group_for_user(user, payload, commit=False)
        )
        result = {
            "action_type": action.action_type,
            "booking_group": domain["item"],
        }
    elif action.action_type == "cancellation":
        from app.booking_v7.routes import cancel_booking_group_for_user

        domain = _domain_result(
            cancel_booking_group_for_user(
                user, int(payload["group_id"]), commit=False
            )
        )
        result = {
            "action_type": action.action_type,
            "booking_group": domain["item"],
        }
    elif action.action_type == "waitlist":
        from app.booking_v7.routes import create_waitlist_for_user

        domain = _domain_result(create_waitlist_for_user(user, payload, commit=False))
        result = {
            "action_type": action.action_type,
            "waitlist_subscription": domain["item"],
        }
    elif action.action_type == "support_handoff":
        ticket = SupportHandoff(
            id=action.id,
            user_id=user.id,
            thread_id=action.thread_id,
            category=payload["category"],
            priority=payload["priority"],
            summary=payload["summary"],
        )
        db.session.add(ticket)
        result = {
            "action_type": action.action_type,
            "ticket_id": ticket.id,
            "status": "open",
        }
    else:
        execution.status = "failed"
        action.status = "failed"
        db.session.commit()
        raise ActionConflict("该类操作的领域服务尚未启用")

    action.status = "executed"
    action.decided_at = now
    execution.status = "completed"
    execution.result = result
    execution.completed_at = now
    db.session.commit()
    return result
