from __future__ import annotations

import json
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
from app.models import AgentPendingAction, AgentRun, AgentThread, SupportHandoff, User
from app.services.permissions import ROLE_ADMIN, roles_required


def _user():
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return None
    user = db.session.get(User, user_id)
    return user if user and user.is_active and user.role == "user" else None


def _error(message, code, status, *, retryable=False):
    return {
        "message": message,
        "error": {"code": code, "message": message, "retryable": retryable},
    }, status


def _enabled():
    return bool(current_app.config.get("AGENT_ENABLED"))


def _owned_thread(thread_id, user):
    return AgentThread.query.filter_by(id=thread_id, user_id=user.id).first()


def _sse(event, payload):
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
            {"messages": [], "active_subject_id": user.id},
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
            "messages": state.get("messages") or [],
            "active_subject_id": state.get("active_subject_id"),
            "pending_actions": [
                {
                    "action_id": row.id,
                    "action_type": row.action_type,
                    "summary": row.summary,
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
        {"messages": [], "active_subject_id": user.id},
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
            result = run_agent(
                message=message,
                messages=state.get("messages") or [],
                user=stream_user,
                thread_id=stream_thread_id,
                run_id=stream_run_id,
            )
            for item in result.get("events") or []:
                yield _sse(item["event"], item["data"])
            answer = result.get("answer") or ""
            for chunk in iter_text_chunks(answer):
                yield _sse("delta", {"content": chunk})
            state["messages"] = result.get("messages") or [
                *(state.get("messages") or []),
                {"role": "user", "content": message},
                {"role": "assistant", "content": answer},
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
            current_app.logger.exception("Agent run failed")
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
        stream_action = db.session.get(AgentPendingAction, stream_action_id)
        stream_user = db.session.get(User, stream_user_id)
        if payload["decision"] == "reject":
            if stream_action.status == "pending":
                stream_action.status = "rejected"
                stream_action.decided_at = datetime.now(timezone.utc)
                db.session.commit()
            yield _sse(
                "done",
                {"action_id": stream_action_id, "status": stream_action.status},
            )
            return
        if not current_app.config.get("AGENT_WRITE_ENABLED"):
            yield _sse(
                "error",
                {
                    "code": "agent_write_disabled",
                    "message": "Agent 写操作当前未启用",
                    "retryable": False,
                },
            )
            return
        try:
            if stream_action.status == "pending":
                stream_action.status = "approved"
                stream_action.decided_at = datetime.now(timezone.utc)
                db.session.flush()
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
            current_app.logger.exception("Agent action failed")
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
            "summary": action.summary,
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
