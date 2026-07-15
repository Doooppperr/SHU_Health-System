from __future__ import annotations

import json
import threading
import time
import uuid
from collections import defaultdict, deque

from flask import Response, current_app, request, stream_with_context
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.orm import joinedload, selectinload

from app.ai import ai_bp
from app.ai.service import (
    AiCompletion,
    AiConfigurationError,
    AiProviderError,
    answer_authenticated_question,
    answer_guest_question,
    build_analysis_messages,
    build_analysis_facts,
    build_authenticated_messages,
    build_guest_messages,
    emergency_reply,
    find_faq_answer,
    format_analysis_context,
    get_ai_client,
    is_emergency_message,
    iter_text_chunks,
    merge_summary_deterministically,
    needs_record_selection,
    parse_safety_completion,
)
from app.ai.rag import (
    RetrievalResult,
    allowed_grounding_ids,
    format_knowledge_context,
    get_knowledge_retriever,
)
from app.extensions import db
from app.models import FriendRelation, HealthIndicator, HealthRecord, User


_rate_buckets = defaultdict(deque)
_rate_lock = threading.Lock()
_RECORD_QUERY_BATCH_SIZE = 400
_MAX_HISTORY_CONTENT_CHARS = 4000
_HISTORY_TRUNCATION_MARKER = "\n…（较早内容已由服务端裁剪）…\n"


def _current_user_optional():
    identity = get_jwt_identity()
    if identity is None:
        return None
    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return None
    return db.session.get(User, user_id)


def _rate_limit_key(user):
    if user:
        return f"user:{user.id}"
    return f"guest:{request.remote_addr or 'unknown'}"


def _is_rate_limited(user):
    limit_key = (
        "AI_AUTH_RATE_LIMIT_PER_MINUTE" if user else "AI_GUEST_RATE_LIMIT_PER_MINUTE"
    )
    limit = int(current_app.config.get(limit_key, 30 if user else 10))
    now = time.monotonic()
    bucket_key = _rate_limit_key(user)
    with _rate_lock:
        bucket = _rate_buckets[bucket_key]
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(now)
    return False


def _json_error(message, code, status, *, retryable=False):
    return {
        "message": message,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }, status


def _parse_json_object():
    payload = request.get_json(silent=True)
    if payload is None:
        return {}, None
    if not isinstance(payload, dict):
        return None, _json_error("request body must be an object", "invalid_request", 400)
    return payload, None


def _parse_history(raw_history):
    if raw_history is None:
        return [], None
    if not isinstance(raw_history, list):
        return None, "history must be a list"

    max_messages = int(current_app.config.get("AI_MAX_HISTORY_MESSAGES", 20))
    if len(raw_history) > max_messages:
        return None, f"history cannot contain more than {max_messages} messages"
    if len(raw_history) % 2 != 0:
        return None, "history must contain complete user/assistant rounds"

    history = []
    for index, item in enumerate(raw_history):
        if not isinstance(item, dict):
            return None, "history item must be an object"
        expected_role = "user" if index % 2 == 0 else "assistant"
        role = item.get("role")
        content = item.get("content")
        if role != expected_role:
            return None, "history roles must alternate user and assistant"
        if not isinstance(content, str) or not content.strip():
            return None, "history content cannot be empty"
        normalized_content = content.strip()
        if len(normalized_content) > _MAX_HISTORY_CONTENT_CHARS:
            available = _MAX_HISTORY_CONTENT_CHARS - len(_HISTORY_TRUNCATION_MARKER)
            head_length = (available + 1) // 2
            tail_length = available - head_length
            normalized_content = (
                normalized_content[:head_length]
                + _HISTORY_TRUNCATION_MARKER
                + normalized_content[-tail_length:]
            )
        history.append({"role": role, "content": normalized_content})
    return history, None


def _parse_record_ids(raw_ids):
    if raw_ids is None:
        return [], None
    if not isinstance(raw_ids, list):
        return None, "selected_record_ids must be a list"

    parsed = []
    seen = set()
    for value in raw_ids:
        if isinstance(value, bool):
            return None, "record id must be a positive integer"
        try:
            record_id = int(value)
        except (TypeError, ValueError):
            return None, "record id must be a positive integer"
        if record_id <= 0:
            return None, "record id must be a positive integer"
        if record_id not in seen:
            seen.add(record_id)
            parsed.append(record_id)
    return parsed, None


def _parse_record_scope(raw_scope):
    if raw_scope is None:
        return None, None
    if not isinstance(raw_scope, dict):
        return None, "record_scope must be an object"
    if set(raw_scope) - {"owner_id", "mode"}:
        return None, "record_scope contains unsupported fields"
    owner_id = raw_scope.get("owner_id")
    if isinstance(owner_id, bool):
        return None, "record_scope owner_id must be a positive integer"
    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError):
        return None, "record_scope owner_id must be a positive integer"
    if owner_id <= 0 or raw_scope.get("mode") != "all_confirmed":
        return None, "record_scope requires a positive owner_id and mode=all_confirmed"
    return {"owner_id": owner_id, "mode": "all_confirmed"}, None


def _authorized_owner_ids(user):
    friend_ids = (
        db.session.query(FriendRelation.friend_user_id)
        .filter_by(user_id=user.id, auth_status=True)
        .all()
    )
    return {user.id, *(row[0] for row in friend_ids)}


def _record_load_options():
    return (
        joinedload(HealthRecord.owner),
        joinedload(HealthRecord.institution),
        selectinload(HealthRecord.indicators).joinedload(
            HealthIndicator.indicator_dict
        ),
    )


def _load_selected_records(user, record_ids):
    if not record_ids:
        return [], None

    authorized_owner_ids = _authorized_owner_ids(user)
    ordered_records = []
    selected_owner_id = None
    for start in range(0, len(record_ids), _RECORD_QUERY_BATCH_SIZE):
        batch = record_ids[start : start + _RECORD_QUERY_BATCH_SIZE]
        loaded = (
            HealthRecord.query.options(*_record_load_options())
            .filter(HealthRecord.id.in_(batch))
            .all()
        )
        by_id = {item.id: item for item in loaded}
        if len(by_id) != len(batch):
            return None, _json_error("record is unavailable", "record_unavailable", 404)

        ordered_batch = [by_id[item_id] for item_id in batch]
        if any(record.owner_id not in authorized_owner_ids for record in ordered_batch):
            return None, _json_error("record is unavailable", "record_unavailable", 404)
        if any(record.status != "confirmed" for record in ordered_batch):
            return None, _json_error(
                "only confirmed records can be analyzed",
                "record_not_confirmed",
                400,
            )
        if any(not record.indicators for record in ordered_batch):
            return None, _json_error(
                "records must contain at least one indicator",
                "record_has_no_indicators",
                400,
            )

        batch_owner_ids = {record.owner_id for record in ordered_batch}
        if len(batch_owner_ids) != 1:
            return None, _json_error(
                "selected records must belong to the same owner",
                "mixed_record_owners",
                400,
            )
        batch_owner_id = next(iter(batch_owner_ids))
        if selected_owner_id is not None and batch_owner_id != selected_owner_id:
            return None, _json_error(
                "selected records must belong to the same owner",
                "mixed_record_owners",
                400,
            )
        selected_owner_id = batch_owner_id
        ordered_records.extend(ordered_batch)

    return ordered_records, None


def _load_record_scope(user, record_scope):
    owner_id = record_scope["owner_id"]
    if owner_id not in _authorized_owner_ids(user):
        return None, _json_error(
            "record scope is unavailable", "record_scope_unavailable", 404
        )
    records = (
        HealthRecord.query.options(*_record_load_options())
        .filter(
            HealthRecord.owner_id == owner_id,
            HealthRecord.status == "confirmed",
            HealthRecord.indicators.any(),
        )
        .order_by(HealthRecord.exam_date.asc(), HealthRecord.id.asc())
        .all()
    )
    if not records:
        return None, _json_error(
            "record scope is unavailable", "record_scope_unavailable", 404
        )
    return records, None


def _indicator_codes_from_records(records):
    return sorted(
        {
            item.indicator_dict.code
            for record in records
            for item in record.indicators
            if item.indicator_dict is not None
        }
    )


def _retrieve_knowledge(user, query, records, *, limit=None):
    retriever = get_knowledge_retriever(current_app)
    return retriever.retrieve(
        query,
        audience="authenticated"
        if user is not None and user.role == "user"
        else "public",
        indicator_codes=_indicator_codes_from_records(records),
        limit=limit,
    )


def _knowledge_context(result):
    return format_knowledge_context(
        result,
        max_chars=int(current_app.config.get("RAG_MAX_CONTEXT_CHARS", 12000)),
    )


def _analysis_retrieval_query(facts):
    ranked = []
    for trend in facts.get("trends", []):
        observations = trend.get("observations") or []
        abnormal_count = sum(1 for item in observations if item.get("abnormal"))
        latest_abnormal = bool(observations and observations[-1].get("abnormal"))
        try:
            percent_change = abs(float(trend.get("percent_change") or 0))
        except (TypeError, ValueError):
            percent_change = 0
        if latest_abnormal or abnormal_count or percent_change >= 10:
            ranked.append(
                (
                    latest_abnormal,
                    abnormal_count,
                    percent_change,
                    f"{trend.get('name', '')} {trend.get('code', '')}",
                )
            )
    ranked.sort(reverse=True)
    prioritized = [item[-1] for item in ranked]
    if not prioritized:
        prioritized = [
            f"{trend.get('name', '')} {trend.get('code', '')}"
            for trend in facts.get("trends", [])
        ]
    if not prioritized:
        for record in facts.get("records", []):
            for item in record.get("indicators", []):
                if item.get("status") == "异常":
                    prioritized.append(f"{item.get('name', '')} {item.get('code', '')}")
    if not prioritized:
        prioritized = [
            f"{item.get('name', '')} {item.get('code', '')}"
            for record in facts.get("records", [])
            for item in record.get("indicators", [])
        ]
    return "体检指标科普、参考范围与一般生活建议：" + "、".join(prioritized[:10])


def _format_record_context(user, records):
    if not records:
        return ""

    owner_label = "本人" if records[0].owner_id == user.id else "已授权亲友"
    sections = [f"档案归属：{owner_label}。共选择 {len(records)} 份档案。"]
    for index, record in enumerate(
        sorted(records, key=lambda item: (item.exam_date, item.id)), start=1
    ):
        institution = record.institution.name if record.institution else "未填写机构"
        lines = [
            f"档案 {index}：体检日期 {record.exam_date.isoformat()}，机构 {institution}。"
        ]
        for item in record.indicators:
            definition = item.indicator_dict
            if definition is None:
                continue
            reference = "未提供"
            if definition.reference_low is not None or definition.reference_high is not None:
                reference = (
                    f"{definition.reference_low if definition.reference_low is not None else '-∞'}"
                    f" ~ {definition.reference_high if definition.reference_high is not None else '+∞'}"
                    f" {definition.unit or ''}"
                ).strip()
            lines.append(
                f"- {definition.name}（{definition.code}）：{item.value} {definition.unit or ''}；"
                f"参考范围 {reference}；系统标记{'异常' if item.is_abnormal else '正常'}。"
            )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _compact_history(history, summary):
    max_messages = int(current_app.config.get("AI_MAX_HISTORY_MESSAGES", 20))
    if len(history) < max_messages:
        return history, summary.strip(), 0
    compacted_count = 2
    return (
        history[compacted_count:],
        merge_summary_deterministically(summary.strip(), history[:compacted_count]),
        compacted_count,
    )


def _validate_chat_request(user, payload):
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return None, _json_error("message is required", "message_required", 400)
    message = message.strip()
    if len(message) > 2000:
        return None, _json_error("message is too long", "message_too_long", 400)

    summary = payload.get("summary") or ""
    if not isinstance(summary, str) or len(summary) > 6000:
        return None, _json_error("summary is invalid or too long", "invalid_summary", 400)

    history, history_error = _parse_history(payload.get("history"))
    if history_error:
        return None, _json_error(history_error, "invalid_history", 400)

    record_ids, record_error = _parse_record_ids(payload.get("selected_record_ids"))
    if record_error:
        return None, _json_error(record_error, "invalid_record_ids", 400)
    record_scope, scope_error = _parse_record_scope(payload.get("record_scope"))
    if scope_error:
        return None, _json_error(scope_error, "invalid_record_scope", 400)
    if record_scope and payload.get("selected_record_ids") is not None:
        return None, _json_error(
            "selected_record_ids and record_scope are mutually exclusive",
            "record_scope_conflict",
            400,
        )
    has_record_context = bool(record_ids or record_scope)
    if user is None and has_record_context:
        return None, _json_error(
            "login is required to use health records", "login_required", 403
        )
    if user is not None and user.role != "user" and has_record_context:
        return None, _json_error(
            "only regular users can use health records with AI",
            "regular_user_required",
            403,
        )
    if has_record_context and payload.get("consent") is not True:
        return None, _json_error(
            "explicit consent is required before sending record data",
            "record_consent_required",
            400,
        )

    records = []
    if record_ids:
        records, load_error = _load_selected_records(user, record_ids)
        if load_error:
            return None, load_error
    elif record_scope:
        records, load_error = _load_record_scope(user, record_scope)
        if load_error:
            return None, load_error

    model_history, updated_summary, compacted_count = _compact_history(history, summary)
    return {
        "message": message,
        "history": model_history,
        "summary": updated_summary,
        "compacted_count": compacted_count,
        "record_ids": record_ids,
        "record_scope": record_scope,
        "records": records,
        "retrieval": RetrievalResult(status="disabled"),
    }, None


def _resolve_chat_locally(user, chat_request):
    message = chat_request["message"]
    if user is not None and is_emergency_message(message):
        return {
            "result": {
                "reply": emergency_reply(),
                "decision": "emergency",
                "usage": {},
            },
            "source": "safety_rule",
            "client": None,
        }
    if (
        user is not None
        and user.role == "user"
        and not chat_request["records"]
        and needs_record_selection(message)
    ):
        return {
            "action": "select_records",
            "message": "需要参考个人档案才能继续，请选择本次要引用的档案。",
            "source": "selection_rule",
            "client": None,
        }

    faq_answer = find_faq_answer(message)
    if faq_answer:
        return {
            "result": {"reply": faq_answer, "decision": "answer", "usage": {}},
            "source": "faq",
            "client": None,
        }

    return None


def _resolve_chat(user, chat_request):
    local_resolution = _resolve_chat_locally(user, chat_request)
    if local_resolution is not None:
        return local_resolution

    message = chat_request["message"]
    chat_request["retrieval"] = _retrieve_knowledge(
        user, message, chat_request["records"]
    )
    retrieval = chat_request["retrieval"]
    knowledge_context = _knowledge_context(retrieval)
    client = get_ai_client(current_app.config)
    support_phone = (current_app.config.get("AI_SUPPORT_PHONE") or "").strip()
    if user is None:
        result = answer_guest_question(
            client,
            message,
            chat_request["history"],
            chat_request["summary"],
            support_phone,
            knowledge_context,
        )
    else:
        result = answer_authenticated_question(
            client,
            message,
            chat_request["history"],
            chat_request["summary"],
            _format_record_context(user, chat_request["records"]),
            support_phone,
            knowledge_context,
            allowed_grounding_ids(retrieval),
        )
    if result.get("decision") == "select_records":
        return {
            "action": "select_records",
            "message": result["reply"],
            "source": "model",
            "client": client,
        }
    return {"result": result, "source": "model", "client": client}


def _chat_response_payload(user, chat_request, resolution):
    result = resolution["result"]
    client = resolution.get("client")
    source = resolution["source"]
    support_phone = (current_app.config.get("AI_SUPPORT_PHONE") or "").strip()
    payload = {
        "reply": result["reply"],
        "decision": result["decision"],
        "source": source,
        "summary": chat_request["summary"],
        "compacted_count": chat_request["compacted_count"],
        "mode": "authenticated" if user else "guest",
        "selected_record_ids": [record.id for record in chat_request["records"]],
        "record_scope": chat_request["record_scope"],
        "model": (
            getattr(client, "model", None)
            or current_app.config.get("DEEPSEEK_MODEL")
        )
        if source == "model"
        else None,
        "usage": result.get("usage") or {},
        "rag_used": chat_request["retrieval"].used,
        "retrieval_status": chat_request["retrieval"].status,
        "knowledge_source_count": len(
            {item.source_id for item in chat_request["retrieval"].hits}
        ),
    }
    if payload["decision"] == "support":
        payload["support_phone"] = support_phone or None
    return payload


def _sse(event, payload):
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _consume_provider_stream(
    client,
    messages,
    *,
    json_output,
    max_tokens,
    heartbeat_message,
    heartbeat_interval=0.25,
):
    """Incrementally drain the provider while retaining unsafe JSON server-side.

    Periodic status events give the WSGI server a write boundary. If the client
    disconnects, GeneratorExit closes the nested provider generator immediately,
    which in turn closes the active upstream response in DeepSeekClient.stream.
    """
    content_parts = []
    usage = {}
    last_heartbeat_at = None
    provider_stream = client.stream(
        messages,
        json_output=json_output,
        max_tokens=max_tokens,
    )
    try:
        for content, event_usage in provider_stream:
            if content:
                content_parts.append(content)
                now = time.monotonic()
                if (
                    last_heartbeat_at is None
                    or now - last_heartbeat_at >= heartbeat_interval
                ):
                    last_heartbeat_at = now
                    yield _sse(
                        "status",
                        {"stage": "deciding", "message": heartbeat_message},
                    )
            if event_usage is not None:
                usage = event_usage
    finally:
        close = getattr(provider_stream, "close", None)
        if callable(close):
            close()

    content = "".join(content_parts).strip()
    if not content:
        raise AiProviderError(
            "AI provider returned an empty response",
            code="provider_empty_response",
            retryable=False,
        )
    return AiCompletion(content=content, usage=usage)


def _stream_model_chat_resolution(user, chat_request):
    client = get_ai_client(current_app.config)
    support_phone = (current_app.config.get("AI_SUPPORT_PHONE") or "").strip()
    message = chat_request["message"]
    retrieval = chat_request["retrieval"]
    knowledge_context = _knowledge_context(retrieval)
    if user is None:
        messages = build_guest_messages(
            message,
            chat_request["history"],
            chat_request["summary"],
            support_phone,
            knowledge_context,
        )
        completion = yield from _consume_provider_stream(
            client,
            messages,
            json_output=False,
            max_tokens=700,
            heartbeat_message="AI 正在生成回复…",
        )
        result = {
            "reply": completion.content,
            "decision": "answer",
            "usage": completion.usage,
        }
    else:
        messages = build_authenticated_messages(
            message,
            chat_request["history"],
            chat_request["summary"],
            _format_record_context(user, chat_request["records"]),
            knowledge_context,
        )
        completion = yield from _consume_provider_stream(
            client,
            messages,
            json_output=True,
            max_tokens=1200,
            heartbeat_message="AI 正在进行安全判断…",
        )
        result = parse_safety_completion(
            completion, support_phone, allowed_grounding_ids(retrieval)
        )

    if result.get("decision") == "select_records":
        return {
            "action": "select_records",
            "message": result["reply"],
            "source": "model",
            "client": client,
        }
    return {"result": result, "source": "model", "client": client}


def _stream_error_payload(exc, request_id):
    if isinstance(exc, AiConfigurationError):
        return {
            "request_id": request_id,
            "code": "ai_not_configured",
            "message": "AI service is not configured",
            "retryable": False,
        }
    if isinstance(exc, AiProviderError):
        message = "AI service is temporarily unavailable"
        if exc.code == "provider_rate_limited":
            message = "AI service is busy, please try again later"
        elif exc.code == "provider_timeout":
            message = "AI response timed out, please try again"
        return {
            "request_id": request_id,
            "code": exc.code,
            "message": message,
            "retryable": exc.retryable,
        }
    return {
        "request_id": request_id,
        "code": "internal_error",
        "message": "AI service is temporarily unavailable",
        "retryable": True,
    }


def _log_stream_completion(
    logger,
    *,
    request_id,
    operation,
    mode,
    record_count,
    prompt_chars,
    started_at,
    first_delta_at,
    status,
    usage,
    retrieval,
):
    now = time.monotonic()
    log_data = {
        "request_id": request_id,
        "operation": operation,
        "mode": mode,
        "record_count": record_count,
        "prompt_chars": prompt_chars,
        "first_delta_ms": (
            round((first_delta_at - started_at) * 1000) if first_delta_at else None
        ),
        "total_ms": round((now - started_at) * 1000),
        "status": status,
        "usage": usage or {},
        "retrieval": retrieval.log_payload(),
    }
    logger.info("ai_request %s", json.dumps(log_data, ensure_ascii=True, separators=(",", ":")))


def _sse_response(generator):
    return Response(
        stream_with_context(generator),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@ai_bp.get("/records")
@jwt_required()
def analyzable_records():
    user = _current_user_optional()
    if user is None:
        return _json_error("user not found", "user_not_found", 404)
    if user.role != "user":
        return _json_error(
            "only regular users can analyze health records",
            "regular_user_required",
            403,
        )

    owner_ids = _authorized_owner_ids(user)
    records = (
        HealthRecord.query.options(
            joinedload(HealthRecord.owner),
            joinedload(HealthRecord.institution),
            selectinload(HealthRecord.indicators),
        )
        .filter(
            HealthRecord.owner_id.in_(owner_ids),
            HealthRecord.status == "confirmed",
            HealthRecord.indicators.any(),
        )
        .order_by(HealthRecord.exam_date.desc(), HealthRecord.id.desc())
        .all()
    )
    items = []
    for record in records:
        items.append(
            {
                "id": record.id,
                "display_id": record.display_id,
                "owner_id": record.owner_id,
                "owner": {
                    "id": record.owner_id,
                    "username": record.owner.username if record.owner else "未知",
                    "label": "本人" if record.owner_id == user.id else "已授权亲友",
                },
                "exam_date": record.exam_date.isoformat(),
                "institution": (
                    {"id": record.institution.id, "name": record.institution.name}
                    if record.institution
                    else None
                ),
                "indicator_count": len(record.indicators),
                "status": record.status,
            }
        )
    owners_by_id = {}
    for item in items:
        owner_id = item["owner_id"]
        summary = owners_by_id.setdefault(
            owner_id,
            {
                "owner_id": owner_id,
                "owner": item["owner"],
                "record_count": 0,
                "date_range": {"first": item["exam_date"], "latest": item["exam_date"]},
            },
        )
        summary["record_count"] += 1
        summary["date_range"]["first"] = min(
            summary["date_range"]["first"], item["exam_date"]
        )
        summary["date_range"]["latest"] = max(
            summary["date_range"]["latest"], item["exam_date"]
        )
    return {"items": items, "owners": list(owners_by_id.values())}, 200


@ai_bp.post("/chat")
@jwt_required(optional=True)
def chat():
    user = _current_user_optional()
    if get_jwt_identity() is not None and user is None:
        return _json_error("user not found", "user_not_found", 404)
    if _is_rate_limited(user):
        return _json_error(
            "AI requests are too frequent, please try again later",
            "rate_limited",
            429,
            retryable=True,
        )
    payload, payload_error = _parse_json_object()
    if payload_error:
        return payload_error
    chat_request, validation_error = _validate_chat_request(user, payload)
    if validation_error:
        return validation_error

    try:
        resolution = _resolve_chat(user, chat_request)
    except AiConfigurationError:
        return _json_error("AI service is not configured", "ai_not_configured", 503)
    except AiProviderError as exc:
        current_app.logger.exception("AI provider request failed")
        return _json_error(
            "AI service is temporarily unavailable",
            exc.code,
            502,
            retryable=exc.retryable,
        )

    if resolution.get("action"):
        return {
            "reply": resolution["message"],
            "decision": "answer",
            "action": resolution["action"],
            "source": resolution["source"],
            "summary": chat_request["summary"],
            "compacted_count": chat_request["compacted_count"],
            "mode": "authenticated" if user else "guest",
            "selected_record_ids": [],
            "model": None,
            "rag_used": False,
            "retrieval_status": "disabled",
            "knowledge_source_count": 0,
        }, 200
    return _chat_response_payload(user, chat_request, resolution), 200


@ai_bp.post("/chat/stream")
@jwt_required(optional=True)
def chat_stream():
    user = _current_user_optional()
    if get_jwt_identity() is not None and user is None:
        return _json_error("user not found", "user_not_found", 404)
    if _is_rate_limited(user):
        return _json_error(
            "AI requests are too frequent, please try again later",
            "rate_limited",
            429,
            retryable=True,
        )
    payload, payload_error = _parse_json_object()
    if payload_error:
        return payload_error
    chat_request, validation_error = _validate_chat_request(user, payload)
    if validation_error:
        return validation_error

    request_id = uuid.uuid4().hex
    mode = "authenticated" if user else "guest"
    configured_model = current_app.config.get("DEEPSEEK_MODEL")
    logger = current_app.logger
    prompt_chars = len(chat_request["message"]) + sum(
        len(item["content"]) for item in chat_request["history"]
    ) + len(chat_request["summary"])

    def generate():
        started_at = time.monotonic()
        first_delta_at = None
        final_status = "cancelled"
        usage = {}
        yield _sse(
            "meta",
            {
                "request_id": request_id,
                "mode": mode,
                "model": configured_model,
            },
        )
        yield _sse(
            "status",
            {"stage": "validating", "message": "请求已验证，正在准备回复…"},
        )
        try:
            resolution = _resolve_chat_locally(user, chat_request)
            if resolution is None:
                yield _sse(
                    "status",
                    {"stage": "retrieving", "message": "正在检索可用知识资料…"},
                )
                chat_request["retrieval"] = _retrieve_knowledge(
                    user, chat_request["message"], chat_request["records"]
                )
                resolution = yield from _stream_model_chat_resolution(
                    user,
                    chat_request,
                )
            if resolution.get("action"):
                yield _sse(
                    "action",
                    {
                        "action": "select_records",
                        "message": resolution["message"],
                    },
                )
                yield _sse(
                    "done",
                    {
                        "request_id": request_id,
                        "decision": "answer",
                        "source": resolution["source"],
                        "summary": chat_request["summary"],
                        "model": (
                            getattr(resolution.get("client"), "model", None)
                            if resolution.get("client") is not None
                            else None
                        ),
                        "rag_used": False,
                        "retrieval_status": "disabled",
                        "knowledge_source_count": 0,
                    },
                )
                final_status = "completed"
                return

            response_payload = _chat_response_payload(user, chat_request, resolution)
            usage = response_payload.pop("usage", {})
            yield _sse(
                "status",
                {"stage": "generating", "message": "AI 已完成安全判断，正在生成回复…"},
            )
            for chunk in iter_text_chunks(response_payload["reply"]):
                if first_delta_at is None:
                    first_delta_at = time.monotonic()
                yield _sse("delta", {"text": chunk})
            done_payload = {
                "request_id": request_id,
                "decision": response_payload["decision"],
                "source": response_payload["source"],
                "summary": response_payload["summary"],
                "model": response_payload["model"],
                "rag_used": response_payload["rag_used"],
                "retrieval_status": response_payload["retrieval_status"],
                "knowledge_source_count": response_payload["knowledge_source_count"],
            }
            if response_payload.get("support_phone"):
                done_payload["support_phone"] = response_payload["support_phone"]
            yield _sse("done", done_payload)
            final_status = "completed"
        except GeneratorExit:
            final_status = "cancelled"
            raise
        except (AiConfigurationError, AiProviderError) as exc:
            logger.warning(
                "ai_stream_failed request_id=%s code=%s",
                request_id,
                getattr(exc, "code", "ai_not_configured"),
            )
            yield _sse("error", _stream_error_payload(exc, request_id))
            final_status = "failed"
        except Exception as exc:  # pragma: no cover - defensive WSGI boundary
            logger.exception("AI stream failed request_id=%s", request_id)
            yield _sse("error", _stream_error_payload(exc, request_id))
            final_status = "failed"
        finally:
            _log_stream_completion(
                logger,
                request_id=request_id,
                operation="chat",
                mode=mode,
                record_count=len(chat_request["records"]),
                prompt_chars=prompt_chars,
                started_at=started_at,
                first_delta_at=first_delta_at,
                status=final_status,
                usage=usage,
                retrieval=chat_request["retrieval"],
            )

    return _sse_response(generate())


@ai_bp.post("/analyze/stream")
@jwt_required()
def analyze_stream():
    user = _current_user_optional()
    if user is None:
        return _json_error("user not found", "user_not_found", 404)
    if user.role != "user":
        return _json_error(
            "only regular users can analyze health records",
            "regular_user_required",
            403,
        )
    if _is_rate_limited(user):
        return _json_error(
            "AI requests are too frequent, please try again later",
            "rate_limited",
            429,
            retryable=True,
        )
    payload, payload_error = _parse_json_object()
    if payload_error:
        return payload_error
    record_ids, record_error = _parse_record_ids(payload.get("selected_record_ids"))
    if record_error:
        return _json_error(record_error, "invalid_record_ids", 400)
    record_scope, scope_error = _parse_record_scope(payload.get("record_scope"))
    if scope_error:
        return _json_error(scope_error, "invalid_record_scope", 400)
    if record_scope and payload.get("selected_record_ids") is not None:
        return _json_error(
            "selected_record_ids and record_scope are mutually exclusive",
            "record_scope_conflict",
            400,
        )
    if not record_ids and not record_scope:
        return _json_error(
            "select at least one record", "records_required", 400
        )
    if payload.get("consent") is not True:
        return _json_error(
            "explicit consent is required before sending record data",
            "record_consent_required",
            400,
        )
    if record_scope:
        records, load_error = _load_record_scope(user, record_scope)
    else:
        records, load_error = _load_selected_records(user, record_ids)
    if load_error:
        return load_error

    facts = build_analysis_facts(user, records)
    request_id = uuid.uuid4().hex
    configured_model = current_app.config.get("DEEPSEEK_MODEL")
    support_phone = (current_app.config.get("AI_SUPPORT_PHONE") or "").strip()
    logger = current_app.logger
    prompt_chars = len(format_analysis_context(facts))

    def generate():
        started_at = time.monotonic()
        first_delta_at = None
        final_status = "cancelled"
        usage = {}
        retrieval = RetrievalResult(status="disabled")
        yield _sse(
            "meta",
            {
                "request_id": request_id,
                "mode": "analysis",
                "model": configured_model,
            },
        )
        yield _sse(
            "status",
            {"stage": "analyzing", "message": "档案已验证，正在计算指标与趋势…"},
        )
        try:
            yield _sse(
                "status",
                {"stage": "retrieving", "message": "正在检索相关健康知识…"},
            )
            retrieval = _retrieve_knowledge(
                user,
                _analysis_retrieval_query(facts),
                records,
                limit=int(current_app.config.get("RAG_ANALYSIS_CONTEXT_K", 6)),
            )
            client = get_ai_client(current_app.config)
            messages = build_analysis_messages(facts, _knowledge_context(retrieval))
            completion = yield from _consume_provider_stream(
                client,
                messages,
                json_output=True,
                max_tokens=2200,
                heartbeat_message="AI 正在进行安全判断…",
            )
            result = parse_safety_completion(
                completion, support_phone, allowed_grounding_ids(retrieval)
            )
            usage = result.get("usage") or {}
            yield _sse(
                "status",
                {"stage": "generating", "message": "AI 已完成安全判断，正在整理分析…"},
            )
            for chunk in iter_text_chunks(result["reply"]):
                if first_delta_at is None:
                    first_delta_at = time.monotonic()
                yield _sse("delta", {"text": chunk})
            done_payload = {
                "request_id": request_id,
                "decision": result["decision"],
                "source": "model",
                "summary": "",
                "model": getattr(client, "model", None) or configured_model,
                "rag_used": retrieval.used,
                "retrieval_status": retrieval.status,
                "knowledge_source_count": len(
                    {item.source_id for item in retrieval.hits}
                ),
            }
            if result["decision"] == "support":
                done_payload["support_phone"] = support_phone or None
            yield _sse("done", done_payload)
            final_status = "completed"
        except GeneratorExit:
            final_status = "cancelled"
            raise
        except (AiConfigurationError, AiProviderError) as exc:
            logger.warning(
                "ai_analysis_failed request_id=%s code=%s",
                request_id,
                getattr(exc, "code", "ai_not_configured"),
            )
            yield _sse("error", _stream_error_payload(exc, request_id))
            final_status = "failed"
        except Exception as exc:  # pragma: no cover - defensive WSGI boundary
            logger.exception("AI analysis failed request_id=%s", request_id)
            yield _sse("error", _stream_error_payload(exc, request_id))
            final_status = "failed"
        finally:
            _log_stream_completion(
                logger,
                request_id=request_id,
                operation="analysis",
                mode="authenticated",
                record_count=len(records),
                prompt_chars=prompt_chars,
                started_at=started_at,
                first_delta_at=first_delta_at,
                status=final_status,
                usage=usage,
                retrieval=retrieval,
            )

    return _sse_response(generate())
