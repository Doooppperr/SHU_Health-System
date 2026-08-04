from __future__ import annotations

import json
import re
import traceback
import uuid
from datetime import datetime, timezone

from flask import Response, current_app, g, request, stream_with_context
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.agent import agent_bp
from app.agent.actions import ActionConflict, execute_approved_action
from app.agent.crypto import decrypt_json, encrypt_json
from app.agent.runtime import run_agent
from app.ai.service import AiConfigurationError, AiProviderError, iter_text_chunks
from app.extensions import db
from app.models import (
    AgentActionExecution,
    AgentPendingAction,
    AgentRun,
    AgentThread,
    SupportHandoff,
    User,
)
from app.services.permissions import ROLE_ADMIN, roles_required
from app.services.sensitive_data import redact_health_identity_codes


HEALTH_ID_PATTERN = re.compile(
    r"HID-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}",
    re.IGNORECASE,
)
PARTICIPANT_TOKEN_PATTERN = re.compile(r"bpt_[A-Za-z0-9_-]{43}")
PARTICIPANT_SLOT_PREFIX = "participant_slot_"
PARTICIPANT_SLOT_PATTERN = re.compile(r"participant_slot_[0-9a-f]{32}")


def _user():
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return None
    user = db.session.get(User, user_id)
    return user if user and user.is_active and user.role == "user" else None


def _error(message, code, status, *, retryable=False):
    return _redact_user_visible_participant_data({
        "message": message,
        "error": {"code": code, "message": message, "retryable": retryable},
    }), status


def _enabled():
    return bool(current_app.config.get("AGENT_ENABLED"))


def _owned_thread(thread_id, user):
    return AgentThread.query.filter_by(id=thread_id, user_id=user.id).first()


def _sse(event, payload):
    event = _redact_user_visible_participant_data(event)
    payload = _redact_user_visible_participant_data(payload)
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_response(generator):
    return Response(
        stream_with_context(generator),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _claim_action_decision(action_id, *, user_id, decision, decided_at):
    """Atomically choose the sole decision allowed to execute an action."""
    next_status = "approved" if decision == "approve" else "rejected"
    changed = AgentPendingAction.query.filter(
        AgentPendingAction.id == action_id,
        AgentPendingAction.user_id == user_id,
        AgentPendingAction.status == "pending",
        AgentPendingAction.expires_at > decided_at,
    ).update(
        {
            AgentPendingAction.status: next_status,
            AgentPendingAction.decided_at: decided_at,
        },
        synchronize_session=False,
    )
    return changed == 1


def _expire_pending_action_cas(action_id, *, user_id, expired_at):
    changed = AgentPendingAction.query.filter(
        AgentPendingAction.id == action_id,
        AgentPendingAction.user_id == user_id,
        AgentPendingAction.status == "pending",
        AgentPendingAction.expires_at <= expired_at,
    ).update(
        {
            AgentPendingAction.status: "expired",
            AgentPendingAction.decided_at: expired_at,
        },
        synchronize_session=False,
    )
    return changed == 1


def _masked_agent_name(value):
    name = str(value or "").strip()
    if not name:
        return "未完善姓名"
    if len(name) == 1:
        return f"{name}*"
    return f"{name[0]}{'*' * (len(name) - 1)}"


def _prepare_agent_message(user, message):
    """Replace raw health IDs before any model call or conversation storage.

    Health identity codes are secure booking inputs, not model context. The
    model receives only the canonical self/linked source or a thread-scoped,
    non-secret slot plus a masked summary. Raw codes and bearer credentials
    never enter the model conversation.
    """
    matches = list(dict.fromkeys(
        match.group(0).upper()
        for match in HEALTH_ID_PATTERN.finditer(message)
    ))
    if not matches:
        return message, {}, None
    if len(matches) > 5:
        return None, {}, _error(
            "一次最多解析5位受检者的健康身份码",
            "BOOKING_PARTICIPANTS_INVALID",
            400,
        )

    from app.services.booking_participants import issue_participant_token

    replacements = {}
    participant_slots = {}
    for health_id in matches:
        item, error = issue_participant_token(user, health_id)
        if error:
            db.session.rollback()
            payload, status = error
            return None, {}, _error(
                payload.get("message") or "无法使用该健康身份码添加受检者，请核对后重试",
                payload.get("code") or "HEALTH_ID_PARTICIPANT_UNAVAILABLE",
                status,
            )
        participant_type = item.get("participant_type")
        if participant_type == "self":
            credential_summary = "participant_type=self；"
        elif participant_type == "linked_account":
            credential_summary = (
                "participant_type=linked_account；"
                f"relation_id={item['relation_id']}；"
            )
        else:
            slot_id = f"{PARTICIPANT_SLOT_PREFIX}{uuid.uuid4().hex}"
            participant_slots[slot_id] = {
                "participant_token": item["participant_token"],
                "expires_at": item["expires_at"],
            }
            credential_summary = (
                "participant_type=health_code_token；"
                f"participant_token={slot_id}；"
            )
        replacements[health_id] = (
            "[健康身份码已由服务端安全解析："
            f"{credential_summary}"
            f"受检者={_masked_agent_name(item.get('real_name'))}；"
            f"性别={item.get('gender') or '未公开'}；"
            f"出生年份={item.get('birth_year') or '未知'}；"
            f"身份码={item.get('masked_health_id') or '已脱敏'}。"
            "不得展示或复述内部参与人凭证。]"
        )

    sanitized = HEALTH_ID_PATTERN.sub(
        lambda match: replacements[match.group(0).upper()],
        message,
    )
    return sanitized, participant_slots, None


def _redact_participant_tokens(value):
    """Remove one-time booking credentials from every user-visible payload."""
    if isinstance(value, str):
        return PARTICIPANT_TOKEN_PATTERN.sub("[安全参与人凭证]", value)
    if isinstance(value, list):
        return [_redact_participant_tokens(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_participant_tokens(item) for item in value)
    if isinstance(value, dict):
        return {
            (
                _redact_participant_tokens(key)
                if isinstance(key, str)
                else key
            ): _redact_participant_tokens(item)
            for key, item in value.items()
        }
    return value


def _redact_user_visible_participant_data(value):
    """Hide bearer tokens and internal slots from every client/log surface."""
    value = redact_health_identity_codes(value)
    value = _redact_participant_tokens(value)
    if isinstance(value, str):
        return PARTICIPANT_SLOT_PATTERN.sub("[安全参与人引用]", value)
    if isinstance(value, list):
        return [_redact_user_visible_participant_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(
            _redact_user_visible_participant_data(item) for item in value
        )
    if isinstance(value, dict):
        return {
            (
                _redact_user_visible_participant_data(key)
                if isinstance(key, str)
                else key
            ): _redact_user_visible_participant_data(item)
            for key, item in value.items()
        }
    return value


def _redact_model_history(value):
    """Remove legacy raw secrets while retaining non-secret participant slots."""
    return redact_health_identity_codes(_redact_participant_tokens(value))


def _active_participant_slots(value):
    """Keep only well-formed, unexpired encrypted slot mappings."""
    if not isinstance(value, dict):
        return {}
    now = datetime.now(timezone.utc)
    active = {}
    for slot_id, row in value.items():
        if (
            not isinstance(slot_id, str)
            or not slot_id.startswith(PARTICIPANT_SLOT_PREFIX)
            or not isinstance(row, dict)
            or not PARTICIPANT_TOKEN_PATTERN.fullmatch(
                str(row.get("participant_token") or "")
            )
        ):
            continue
        try:
            expires_at = datetime.fromisoformat(str(row.get("expires_at") or ""))
        except ValueError:
            continue
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            continue
        active[slot_id] = {
            "participant_token": row["participant_token"],
            "expires_at": expires_at.isoformat(),
        }
    return active


def _log_sanitized_exception(message):
    """Retain a useful traceback without writing bearer credentials."""
    current_app.logger.error(
        "%s\n%s",
        message,
        _redact_user_visible_participant_data(traceback.format_exc()),
    )


@agent_bp.get("/capabilities")
def capabilities():
    return {
        "enabled": _enabled(),
        "write_enabled": bool(current_app.config.get("AGENT_WRITE_ENABLED")),
        "router": {
            "mode": "deepseek",
            "local_small_llm_enabled": bool(
                current_app.config.get("AGENT_ROUTER_ENABLED")
            ),
        },
        "model": str(current_app.config.get("DEEPSEEK_MODEL") or ""),
        "provider_mode": "mock" if current_app.config.get("AI_USE_MOCK") else "live",
        "tools": {
            "records": True,
            "trends": True,
            "institutions": True,
            "packages": True,
            "booking_drafts": bool(current_app.config.get("AGENT_WRITE_ENABLED")),
            "support_handoff": bool(current_app.config.get("AGENT_WRITE_ENABLED")),
        },
        "legacy_fallback": "/api/ai",
    }


@agent_bp.post("/threads")
@jwt_required()
def create_thread():
    if not _enabled():
        return _error("Agent 功能当前未启用", "agent_disabled", 503)
    user = _user()
    if user is None:
        return _error("仅普通用户可以创建 Agent 会话", "forbidden", 403)
    thread_id = str(uuid.uuid4())
    thread = AgentThread(
        id=thread_id,
        user_id=user.id,
        encrypted_state=encrypt_json(
            {
                "messages": [],
                "active_subject_id": user.id,
                "participant_slots": {},
            },
            purpose=f"agent-thread:{thread_id}",
        ),
    )
    db.session.add(thread)
    db.session.commit()
    return {
        "item": {
            "id": thread.id,
            "status": thread.status,
            "created_at": thread.created_at.isoformat(),
        }
    }, 201


@agent_bp.get("/threads/<string:thread_id>")
@jwt_required()
def get_thread(thread_id):
    user = _user()
    thread = _owned_thread(thread_id, user) if user else None
    if thread is None:
        return _error("没有找到 Agent 会话", "thread_not_found", 404)
    state = decrypt_json(thread.encrypted_state, purpose=f"agent-thread:{thread.id}")
    pending = AgentPendingAction.query.filter_by(
        thread_id=thread.id, user_id=user.id, status="pending"
    ).all()
    return {
        "item": {
            "id": thread.id,
            "status": thread.status,
            "messages": _redact_user_visible_participant_data(
                state.get("messages") or []
            ),
            "active_subject_id": state.get("active_subject_id"),
            "pending_actions": [
                {
                    "action_id": row.id,
                    "action_type": row.action_type,
                    "summary": _redact_user_visible_participant_data(
                        row.summary
                    ),
                    "expires_at": row.expires_at.isoformat(),
                }
                for row in pending
            ],
            "last_activity_at": thread.last_activity_at.isoformat(),
        }
    }


@agent_bp.delete("/threads/<string:thread_id>")
@jwt_required()
def clear_thread(thread_id):
    user = _user()
    thread = _owned_thread(thread_id, user) if user else None
    if thread is None:
        return _error("没有找到 Agent 会话", "thread_not_found", 404)
    now = datetime.now(timezone.utc)
    thread.status = "cleared"
    thread.encrypted_state = encrypt_json(
        {
            "messages": [],
            "active_subject_id": user.id,
            "participant_slots": {},
        },
        purpose=f"agent-thread:{thread.id}",
    )
    thread.cleared_at = now
    thread.last_activity_at = now
    AgentPendingAction.query.filter_by(
        thread_id=thread.id, user_id=user.id, status="pending"
    ).update({"status": "expired"})
    db.session.commit()
    return "", 204


@agent_bp.post("/threads/<string:thread_id>/runs/stream")
@jwt_required()
def run_stream(thread_id):
    if not _enabled():
        return _error("Agent 功能当前未启用", "agent_disabled", 503)
    user = _user()
    thread = _owned_thread(thread_id, user) if user else None
    if thread is None or thread.status != "active":
        return _error("没有找到可用的 Agent 会话", "thread_not_found", 404)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("请求内容必须是对象", "invalid_request", 400)
    message = str(payload.get("message") or "").strip()
    if not 1 <= len(message) <= 4000:
        return _error("问题长度必须在 1 到 4000 个字符之间", "invalid_message", 400)
    model_message, new_participant_slots, preparation_error = (
        _prepare_agent_message(user, message)
    )
    if preparation_error:
        return preparation_error

    run_id = str(uuid.uuid4())
    run = AgentRun(
        id=run_id,
        thread_id=thread.id,
        user_id=user.id,
        model_name=str(current_app.config.get("DEEPSEEK_MODEL") or ""),
        prompt_version=str(current_app.config.get("AGENT_PROMPT_VERSION") or "agent-v1"),
    )
    db.session.add(run)
    db.session.commit()
    stream_thread_id = thread.id
    stream_run_id = run.id
    stream_user_id = user.id

    def generate():
        yield _sse("meta", {"thread_id": stream_thread_id, "run_id": stream_run_id})
        try:
            stream_thread = db.session.get(AgentThread, stream_thread_id)
            stream_run = db.session.get(AgentRun, stream_run_id)
            stream_user = db.session.get(User, stream_user_id)
            state = decrypt_json(
                stream_thread.encrypted_state,
                purpose=f"agent-thread:{stream_thread_id}",
            )
            participant_slots = _active_participant_slots(
                state.get("participant_slots")
            )
            participant_slots.update(new_participant_slots)
            state["participant_slots"] = participant_slots
            # Threads created by an older release may still have placed a
            # bearer in model-facing history. Drop it before this release ever
            # sends that history to a provider.
            state["messages"] = _redact_model_history(
                state.get("messages") or []
            )
            result = run_agent(
                message=model_message,
                messages=state["messages"],
                participant_slots=participant_slots,
                user=stream_user,
                thread_id=stream_thread_id,
                run_id=stream_run_id,
            )
            for item in result.get("events") or []:
                yield _sse(
                    item["event"],
                    _redact_user_visible_participant_data(item["data"]),
                )
            answer = _redact_user_visible_participant_data(
                result.get("answer") or ""
            )
            for chunk in iter_text_chunks(answer):
                yield _sse("delta", {"content": chunk})
            result_messages = result.get("messages") or [
                *(state.get("messages") or []),
                {"role": "user", "content": model_message},
                {"role": "assistant", "content": answer},
            ]
            # Model-facing history contains only non-secret slot identifiers.
            # The short-lived bearer stays solely in the separate encrypted
            # participant_slots map and is resolved immediately before a tool
            # executes.
            state["messages"] = [
                _redact_model_history(row)
                if isinstance(row, dict)
                else row
                for row in result_messages
            ]
            stream_thread.encrypted_state = encrypt_json(
                state, purpose=f"agent-thread:{stream_thread_id}"
            )
            stream_thread.last_activity_at = datetime.now(timezone.utc)
            stream_run.intent = result.get("intent")
            stream_run.usage = result.get("usage") or {}
            has_approval = any(
                item.get("event") == "approval_required"
                for item in result.get("events") or []
            )
            stream_run.status = "waiting_approval" if has_approval else "completed"
            stream_run.completed_at = datetime.now(timezone.utc)
            db.session.commit()
            yield _sse(
                "done",
                {
                    "thread_id": stream_thread_id,
                    "run_id": stream_run_id,
                    "status": stream_run.status,
                    "usage": stream_run.usage,
                },
            )
        except (AiConfigurationError, AiProviderError) as exc:
            db.session.rollback()
            failed = db.session.get(AgentRun, stream_run_id)
            failed.status = "failed"
            failed.error_code = getattr(exc, "code", "provider_unavailable")
            failed.completed_at = datetime.now(timezone.utc)
            db.session.commit()
            yield _sse(
                "error",
                {
                    "code": failed.error_code,
                    "message": "智能服务暂时不可用，请稍后重试",
                    "retryable": getattr(exc, "retryable", True),
                },
            )
        except Exception:
            _log_sanitized_exception("Agent run failed")
            db.session.rollback()
            failed = db.session.get(AgentRun, stream_run_id)
            failed.status = "failed"
            failed.error_code = "agent_internal_error"
            failed.completed_at = datetime.now(timezone.utc)
            db.session.commit()
            yield _sse(
                "error",
                {
                    "code": "agent_internal_error",
                    "message": "Agent 执行失败，请稍后重试",
                    "retryable": True,
                },
            )

    return _stream_response(generate())


@agent_bp.post("/actions/<string:action_id>/decision/stream")
@jwt_required()
def decide_action(action_id):
    user = _user()
    if user is None:
        return _error("仅普通用户可以确认 Agent 操作", "forbidden", 403)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("decision") not in {"approve", "reject"}:
        return _error("decision 必须是 approve 或 reject", "invalid_decision", 400)
    action = AgentPendingAction.query.filter_by(id=action_id, user_id=user.id).first()
    if action is None:
        return _error("没有找到待确认操作", "action_not_found", 404)

    stream_action_id = action.id
    stream_thread_id = action.thread_id
    stream_user_id = user.id

    def generate():
        yield _sse("meta", {"action_id": stream_action_id, "thread_id": stream_thread_id})
        decision = payload["decision"]
        if decision == "approve" and not current_app.config.get("AGENT_WRITE_ENABLED"):
            yield _sse(
                "error",
                {
                    "code": "agent_write_disabled",
                    "message": "Agent 写操作当前未启用",
                    "retryable": False,
                },
            )
            return

        decided_at = datetime.now(timezone.utc)
        if not _claim_action_decision(
            stream_action_id,
            user_id=stream_user_id,
            decision=decision,
            decided_at=decided_at,
        ):
            db.session.rollback()
            current = AgentPendingAction.query.filter_by(
                id=stream_action_id,
                user_id=stream_user_id,
            ).first()
            if current is None:
                yield _sse(
                    "error",
                    {
                        "code": "action_not_found",
                        "message": "没有找到待确认操作",
                        "retryable": False,
                    },
                )
                return
            expires_at = current.expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if current.status == "pending" and expires_at <= decided_at:
                _expire_pending_action_cas(
                    stream_action_id,
                    user_id=stream_user_id,
                    expired_at=decided_at,
                )
                db.session.commit()
                yield _sse(
                    "error",
                    {
                        "code": "action_conflict",
                        "message": "操作草稿已经过期，请重新发起",
                        "retryable": False,
                    },
                )
                return
            if decision == "reject" and current.status == "rejected":
                yield _sse(
                    "done",
                    {"action_id": stream_action_id, "status": "rejected"},
                )
                return
            if decision == "approve" and current.status == "executed":
                execution = AgentActionExecution.query.filter_by(
                    action_id=stream_action_id,
                    status="completed",
                ).first()
                result = execution.result if execution is not None else {}
                yield _sse(
                    "done",
                    {
                        "action_id": stream_action_id,
                        "status": "executed",
                        "result": result,
                    },
                )
                return
            yield _sse(
                "error",
                {
                    "code": "action_conflict",
                    "message": "操作草稿已经被其他请求处理",
                    "retryable": False,
                },
            )
            return

        db.session.expire_all()
        stream_action = db.session.get(AgentPendingAction, stream_action_id)
        stream_user = db.session.get(User, stream_user_id)
        if decision == "reject":
            db.session.commit()
            yield _sse(
                "done",
                {"action_id": stream_action_id, "status": "rejected"},
            )
            return
        try:
            yield _sse("status", {"stage": "write_commit", "message": "正在重新校验并执行"})
            result = execute_approved_action(stream_action, stream_user)
            yield _sse(
                "evidence",
                {"tool": f"commit_{stream_action.action_type}", "result": result},
            )
            yield _sse(
                "done",
                {"action_id": stream_action_id, "status": "executed", "result": result},
            )
        except (ActionConflict, PermissionError) as exc:
            db.session.rollback()
            yield _sse(
                "error",
                {"code": "action_conflict", "message": str(exc), "retryable": False},
            )
        except Exception:
            _log_sanitized_exception("Agent action failed")
            db.session.rollback()
            yield _sse(
                "error",
                {
                    "code": "action_failed",
                    "message": "操作没有完成，请重新查询状态后再试",
                    "retryable": True,
                },
            )

    return _stream_response(generate())


@agent_bp.get("/actions/<string:action_id>")
@jwt_required()
def get_action(action_id):
    user = _user()
    action = (
        AgentPendingAction.query.filter_by(id=action_id, user_id=user.id).first()
        if user
        else None
    )
    if action is None:
        return _error("没有找到 Agent 操作", "action_not_found", 404)
    return {
        "item": {
            "id": action.id,
            "thread_id": action.thread_id,
            "action_type": action.action_type,
            "summary": _redact_user_visible_participant_data(action.summary),
            "status": action.status,
            "expires_at": action.expires_at.isoformat(),
        }
    }


def _ticket_item(row):
    owner = db.session.get(User, row.user_id)
    assignee = (
        db.session.get(User, row.assigned_to_user_id)
        if row.assigned_to_user_id
        else None
    )
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": owner.username if owner else None,
        "thread_id": row.thread_id,
        "category": row.category,
        "priority": row.priority,
        "status": row.status,
        "summary": row.summary,
        "assigned_to_user_id": row.assigned_to_user_id,
        "assigned_to_username": assignee.username if assignee else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


@agent_bp.get("/support-handoffs")
@jwt_required()
def my_support_handoffs():
    user = _user()
    if user is None:
        return _error("仅普通用户可以查看自己的人工工单", "forbidden", 403)
    rows = (
        SupportHandoff.query.filter_by(user_id=user.id)
        .order_by(SupportHandoff.created_at.desc())
        .limit(100)
        .all()
    )
    return {"items": [_ticket_item(row) for row in rows]}


@agent_bp.get("/admin/support-handoffs")
@roles_required(ROLE_ADMIN)
def admin_support_handoffs():
    query = SupportHandoff.query
    status = str(request.args.get("status") or "").strip()
    priority = str(request.args.get("priority") or "").strip()
    if status:
        if status not in {"open", "in_progress", "resolved", "closed"}:
            return _error("无效的工单状态", "invalid_status", 400)
        query = query.filter_by(status=status)
    if priority:
        if priority not in {"normal", "high", "urgent"}:
            return _error("无效的工单优先级", "invalid_priority", 400)
        query = query.filter_by(priority=priority)
    rows = query.order_by(
        SupportHandoff.created_at.desc()
    ).limit(500).all()
    return {"items": [_ticket_item(row) for row in rows]}


@agent_bp.patch("/admin/support-handoffs/<string:ticket_id>")
@roles_required(ROLE_ADMIN)
def update_support_handoff(ticket_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("请求内容必须是对象", "invalid_request", 400)
    row = db.session.get(SupportHandoff, ticket_id)
    if row is None:
        return _error("没有找到人工工单", "ticket_not_found", 404)

    if "status" in payload:
        status = str(payload["status"])
        if status not in {"open", "in_progress", "resolved", "closed"}:
            return _error("无效的工单状态", "invalid_status", 400)
        row.status = status
        row.resolved_at = (
            datetime.now(timezone.utc)
            if status in {"resolved", "closed"}
            else None
        )
    if "assigned_to_user_id" in payload:
        value = payload["assigned_to_user_id"]
        if value in {None, ""}:
            row.assigned_to_user_id = None
        else:
            try:
                assignee_id = int(value)
            except (TypeError, ValueError):
                return _error("处理人账号无效", "invalid_assignee", 400)
            assignee = db.session.get(User, assignee_id)
            if assignee is None or not assignee.is_active or assignee.role != ROLE_ADMIN:
                return _error("处理人必须是启用中的系统管理员", "invalid_assignee", 400)
            row.assigned_to_user_id = assignee.id
    elif row.assigned_to_user_id is None and row.status == "in_progress":
        row.assigned_to_user_id = g.current_user.id
    row.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"item": _ticket_item(row)}
