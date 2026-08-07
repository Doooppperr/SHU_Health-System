from __future__ import annotations

import json
import re
import time
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
    build_trend_analysis_messages,
    find_faq_answer,
    format_analysis_context,
    get_ai_client,
    iter_text_chunks,
    merge_summary_deterministically,
    needs_record_selection,
    parse_model_completion,
)
from app.ai.rag import (
    RetrievalResult,
    allowed_grounding_ids,
    format_knowledge_context,
    get_knowledge_retriever,
)
from app.extensions import db
from app.models import Appointment, HealthDomain, HealthIndicator, HealthRecord, IndicatorDict, IndicatorDomainLink, Institution, ReportAsset, User
from app.services.indicator_values import result_status_is_displayable
from app.services.ai_rate_limit import ai_rate_limited
from app.services.platform_contact import PLATFORM_CONTACT
from app.services.sensitive_data import redact_health_identity_codes


BUSINESS_TZ = ZoneInfo("Asia/Shanghai")
_RECORD_QUERY_BATCH_SIZE = 400
_MAX_HISTORY_CONTENT_CHARS = 4000
_HISTORY_TRUNCATION_MARKER = "\n…（较早内容已由服务端裁剪）…\n"
_MAX_RECORD_CONTEXT_CHARS = 48000
_ACTIVE_SCOPE_MODES = {"selected_records", "all_confirmed", "indicator_history"}
_RECORD_QUERY_TOKENS = (
    "档案", "报告", "体检", "检查结果", "指标", "趋势", "变化", "参考范围",
    "偏高", "偏低", "异常", "肝功能", "肾功能", "血脂", "血糖", "血压",
)
_FOLLOW_UP_TOKENS = ("这个", "这份", "其中", "刚才", "继续", "上述", "该报告", "它")
_TREND_TOKENS = ("趋势", "变化", "对比", "历史", "最近几年", "近几年", "历年")
_LATEST_TOKENS = ("上一次", "最近一次", "最新", "上一份", "最近一份")
_ALL_HISTORY_TOKENS = ("全部历史", "所有历史", "全部报告", "所有报告", "历次报告")


class _HealthIdRedactingAiClient:
    """Enforce health-ID removal at the final external-provider boundary."""

    def __init__(self, client):
        self._client = client
        self.model = getattr(client, "model", None)

    def complete(self, messages, *, json_output=False, max_tokens=1200):
        completion = self._client.complete(
            redact_health_identity_codes(messages),
            json_output=json_output,
            max_tokens=max_tokens,
        )
        return AiCompletion(
            content=redact_health_identity_codes(completion.content),
            usage=redact_health_identity_codes(completion.usage),
        )

    def stream(self, messages, *, json_output=False, max_tokens=1200):
        return self._client.stream(
            redact_health_identity_codes(messages),
            json_output=json_output,
            max_tokens=max_tokens,
        )


def _get_health_id_safe_ai_client():
    return _HealthIdRedactingAiClient(get_ai_client(current_app.config))


def _current_user_optional():
    identity = get_jwt_identity()
    if identity is None:
        return None
    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return None
    user = db.session.get(User, user_id)
    return user if user is not None and user.is_active else None


def _is_rate_limited(user):
    return ai_rate_limited(user)


def _json_error(message, code, status, *, retryable=False):
    return redact_health_identity_codes({
        "message": message,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }), status


def _parse_json_object():
    payload = request.get_json(silent=True)
    if payload is None:
        return {}, None
    if not isinstance(payload, dict):
        return None, _json_error("request body must be an object", "invalid_request", 400)
    # This is the first application boundary after Flask's JSON parser.  Every
    # downstream consumer receives a detached, recursively redacted payload.
    return redact_health_identity_codes(payload), None


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
        normalized_content = redact_health_identity_codes(content.strip())
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


def _parse_active_record_context(raw_context):
    if raw_context is None:
        return None, None
    if not isinstance(raw_context, dict):
        return None, "active_record_context must be an object"
    allowed = {
        "owner_id", "owner_name", "anchor_record_ids", "scope_mode",
        "indicator_codes", "source", "display_summary", "updated_at",
    }
    if set(raw_context) - allowed:
        return None, "active_record_context contains unsupported fields"
    owner_id = raw_context.get("owner_id")
    if isinstance(owner_id, bool):
        return None, "active_record_context owner_id must be a positive integer"
    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError):
        return None, "active_record_context owner_id must be a positive integer"
    if owner_id <= 0:
        return None, "active_record_context owner_id must be a positive integer"
    record_ids, record_error = _parse_record_ids(
        raw_context.get("anchor_record_ids") or []
    )
    if record_error:
        return None, record_error
    scope_mode = raw_context.get("scope_mode") or "selected_records"
    if scope_mode not in _ACTIVE_SCOPE_MODES:
        return None, "active_record_context scope_mode is unsupported"
    if scope_mode == "selected_records" and not record_ids:
        return None, "selected_records context requires anchor_record_ids"
    raw_codes = raw_context.get("indicator_codes") or []
    if not isinstance(raw_codes, list):
        return None, "active_record_context indicator_codes must be a list"
    indicator_codes = []
    for value in raw_codes:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 40:
            return None, "active_record_context indicator code is invalid"
        code = value.strip().upper()
        if code not in indicator_codes:
            indicator_codes.append(code)
    indicator_codes = _canonical_indicator_codes(indicator_codes)
    return {
        "owner_id": owner_id,
        "anchor_record_ids": record_ids,
        "scope_mode": scope_mode,
        "indicator_codes": indicator_codes,
    }, None


def _message_requests_records(message):
    normalized = message.lower()
    return (
        needs_record_selection(message)
        or any(token.lower() in normalized for token in _RECORD_QUERY_TOKENS)
        or any(token.lower() in normalized for token in _FOLLOW_UP_TOKENS)
    )


def _message_requests_trend(message):
    return any(token in message for token in _TREND_TOKENS)


def _message_year(message):
    match = re.search(r"(?<!\d)(20\d{2})\s*年?", message)
    if match:
        return int(match.group(1))
    today_year = datetime.now(BUSINESS_TZ).year
    if "前年" in message:
        return today_year - 2
    if "去年" in message:
        return today_year - 1
    if "今年" in message:
        return today_year
    return None


def _mentioned_owner_id(user, message):
    # A delegated JWT already identifies the switched-to account. Names in a
    # prompt must never act as an alternate health-record selector.
    if (
        any(token in message for token in ("我", "我的", "本人", "当前账号"))
        or (user.real_name and user.real_name in message)
    ):
        return user.id, None
    return None, None


def _indicator_codes_from_message(message):
    matched = []
    normalized = message.lower()
    for definition in IndicatorDict.query.all():
        candidates = [definition.code, definition.name, *(definition.aliases or [])]
        if any(
            isinstance(candidate, str)
            and len(candidate.strip()) >= 2
            and candidate.strip().lower() in normalized
            for candidate in candidates
        ):
            matched.append(definition.code)
    return sorted(set(matched))


def _canonical_indicator_codes(values):
    definitions = IndicatorDict.query.all()
    by_code = {item.code.upper(): item.code for item in definitions}
    by_compact = {}
    for definition in definitions:
        for candidate in [
            definition.code,
            definition.name,
            *(definition.aliases or []),
        ]:
            if isinstance(candidate, str):
                compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", candidate.lower())
                if compact:
                    by_compact.setdefault(compact, definition.code)
    result = []
    for raw in values:
        upper = str(raw).strip().upper()
        canonical = by_code.get(upper)
        if canonical is None and upper.endswith("_C"):
            canonical = by_code.get(upper[:-2])
        if canonical is None:
            compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", upper.lower())
            canonical = by_compact.get(compact)
            if canonical is None and compact.endswith("c"):
                canonical = by_compact.get(compact[:-1])
        if canonical and canonical not in result:
            result.append(canonical)
    return result


def _query_owner_records(owner_id, *, year=None, latest_only=False):
    query = HealthRecord.query.options(*_record_load_options()).filter(
        HealthRecord.owner_id == owner_id,
        HealthRecord.status == "published",
        HealthRecord.indicators.any(),
    )
    if year is not None:
        query = query.filter(
            HealthRecord.exam_date >= date(year, 1, 1),
            HealthRecord.exam_date <= date(year, 12, 31),
        )
    query = query.order_by(HealthRecord.exam_date.desc(), HealthRecord.id.desc())
    if latest_only:
        query = query.limit(1)
    return query.all()


def _authorized_owner_ids(user):
    """Health context is scoped to the effective JWT account only."""
    return {user.id}


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
        if any(record.status != "published" for record in ordered_batch):
            return None, _json_error(
                "only published institution reports can be analyzed",
                "report_not_published",
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
            HealthRecord.status == "published",
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


def _auto_select_records(user, message):
    if user is None or user.role != "user" or not needs_record_selection(message):
        return []
    limit = 3 if any(token in message for token in ("趋势", "变化", "对比", "最近几年", "历史")) else 1
    return (
        HealthRecord.query.options(*_record_load_options())
        .filter(
            HealthRecord.owner_id == user.id,
            HealthRecord.status == "published",
            HealthRecord.indicators.any(),
        )
        .order_by(HealthRecord.exam_date.desc(), HealthRecord.id.desc())
        .limit(limit)
        .all()
    )


def _record_resolution(user, records, *, source, scope_mode, indicator_codes=None):
    if not records:
        return None, None
    ordered = sorted(records, key=lambda item: (item.exam_date, item.id))
    owner = ordered[0].owner
    owner_name = owner.real_name if owner and owner.real_name else (
        "当前账号"
    )
    record_rows = [
        {
            "id": record.id,
            "exam_date": record.exam_date.isoformat(),
            "institution_name": record.institution.name
            if record.institution
            else "未填写机构",
        }
        for record in ordered
    ]
    codes = sorted(set(indicator_codes or []))
    resolution = {
        "source": source,
        "owner": {"id": ordered[0].owner_id, "display_name": owner_name},
        "scope_mode": scope_mode,
        "anchor_record_ids": [record.id for record in ordered[-3:]]
        if scope_mode != "selected_records"
        else [record.id for record in ordered],
        "record_count": len(ordered),
        "date_range": {
            "start": ordered[0].exam_date.isoformat(),
            "end": ordered[-1].exam_date.isoformat(),
        },
        "indicators": codes,
        "records": record_rows[-10:],
        "records_truncated": len(record_rows) > 10,
    }
    display = (
        f"{owner_name} · "
        + (
            f"{'、'.join(codes)} 趋势 · {len(ordered)}份报告"
            if scope_mode == "indicator_history" and codes
            else (
                f"全部历史 · {len(ordered)}份报告"
                if scope_mode == "all_confirmed"
                else (
                    f"{ordered[-1].exam_date.isoformat()} 体检报告"
                    if len(ordered) == 1
                    else f"{len(ordered)}份体检报告"
                )
            )
        )
    )
    next_context = {
        "owner_id": ordered[0].owner_id,
        "owner_name": owner_name,
        "anchor_record_ids": resolution["anchor_record_ids"],
        "scope_mode": scope_mode,
        "indicator_codes": codes,
        "source": source,
        "display_summary": display,
        "updated_at": int(time.time()),
    }
    return resolution, next_context


def _resolve_record_context(user, payload, message, record_ids, record_scope):
    active_context, active_error = _parse_active_record_context(
        payload.get("active_record_context")
    )
    if active_error:
        return None, _json_error(
            active_error, "invalid_active_record_context", 400
        )

    if user is None:
        if record_ids or record_scope or active_context:
            return None, _json_error(
                "login is required to use health records", "login_required", 403
            )
        return {
            "records": [],
            "record_scope": None,
            "resolution": None,
            "next_context": None,
            "record_context": "",
            "indicator_codes": [],
            "auto_selected": False,
        }, None
    if user.role != "user" and (record_ids or record_scope or active_context):
        return None, _json_error(
            "only regular users can use health records with AI",
            "regular_user_required",
            403,
        )

    authorized_owner_ids = _authorized_owner_ids(user) if user.role == "user" else set()
    if active_context and active_context["owner_id"] not in authorized_owner_ids:
        return None, _json_error(
            "当前档案不可用或授权已失效，请重新选择",
            "record_unavailable",
            404,
        )

    records = []
    source = None
    scope_mode = "selected_records"
    indicator_codes = []
    semantic = False
    record_query = user.role == "user" and _message_requests_records(message)

    if record_ids:
        records, load_error = _load_selected_records(user, record_ids)
        if load_error:
            return None, load_error
        source = "manual"
    elif record_scope:
        records, load_error = _load_record_scope(user, record_scope)
        if load_error:
            return None, load_error
        source = "manual"
        scope_mode = "all_confirmed"
    elif user.role == "user" and record_query:
        explicit_owner_id, owner_error = _mentioned_owner_id(user, message)
        if owner_error:
            return None, owner_error
        owner_id = explicit_owner_id or (
            active_context["owner_id"] if active_context else user.id
        )
        if owner_id not in authorized_owner_ids:
            return None, _json_error(
                "当前档案不可用或授权已失效，请重新选择",
                "record_unavailable",
                404,
            )
        year = _message_year(message)
        requested_codes = _indicator_codes_from_message(message)
        indicator_codes = requested_codes or (
            active_context["indicator_codes"] if active_context else []
        )
        all_history = any(token in message for token in _ALL_HISTORY_TOKENS)
        trend = _message_requests_trend(message) and not all_history
        explicit_switch = explicit_owner_id is not None or year is not None

        if all_history:
            records = _query_owner_records(owner_id, year=year)
            scope_mode = "all_confirmed"
        elif trend:
            records = _query_owner_records(owner_id, year=year)
            scope_mode = "indicator_history"
        elif year is not None:
            records = _query_owner_records(owner_id, year=year)
            scope_mode = "selected_records"
        elif (
            active_context
            and not explicit_switch
            and any(token in message for token in _FOLLOW_UP_TOKENS)
            and active_context["anchor_record_ids"]
        ):
            records, load_error = _load_selected_records(
                user, active_context["anchor_record_ids"]
            )
            if load_error:
                return None, load_error
            scope_mode = (
                "selected_records"
                if active_context["scope_mode"] == "indicator_history"
                else active_context["scope_mode"]
            )
        elif active_context and not explicit_switch and not any(
            token in message for token in _LATEST_TOKENS
        ):
            if active_context["scope_mode"] == "all_confirmed":
                records = _query_owner_records(owner_id)
                scope_mode = "all_confirmed"
            else:
                records, load_error = _load_selected_records(
                    user, active_context["anchor_record_ids"]
                )
                if load_error:
                    return None, load_error
                scope_mode = "selected_records"
        else:
            records = _query_owner_records(owner_id, latest_only=True)
            scope_mode = "selected_records"
        source = "inherited" if active_context and not explicit_switch else "semantic"
        semantic = True

    if record_query and not records:
        return {
            "records": [],
            "record_scope": None,
            "resolution": None,
            "next_context": active_context,
            "record_context": "",
            "indicator_codes": [],
            "auto_selected": False,
            "no_match": True,
        }, None

    resolution = next_context = None
    context_text = ""
    if records:
        if len({record.owner_id for record in records}) != 1:
            return None, _json_error(
                "selected records must belong to the same owner",
                "mixed_record_owners",
                400,
            )
        if indicator_codes:
            available_codes = {
                item.indicator_dict.code
                for record in records
                for item in record.indicators
                if item.indicator_dict is not None
            }
            indicator_codes = [
                code for code in indicator_codes if code in available_codes
            ]
            if scope_mode == "indicator_history":
                selected_code_set = set(indicator_codes)
                records = [
                    record
                    for record in records
                    if any(
                        item.indicator_dict is not None
                        and item.indicator_dict.code in selected_code_set
                        for item in record.indicators
                    )
                ]
        if not records:
            return {
                "records": [],
                "record_scope": None,
                "resolution": None,
                "next_context": active_context,
                "record_context": "",
                "indicator_codes": [],
                "auto_selected": False,
                "no_match": True,
            }, None
        resolution, next_context = _record_resolution(
            user,
            records,
            source=source or "manual",
            scope_mode=scope_mode,
            indicator_codes=indicator_codes,
        )
        context_text = _format_record_context(
            user, records, indicator_codes=indicator_codes
        )
    elif active_context:
        next_context = {
            **active_context,
            "source": "inherited",
            "updated_at": int(time.time()),
        }
    return {
        "records": records,
        "record_scope": record_scope,
        "resolution": resolution,
        "next_context": next_context,
        "record_context": context_text,
        "indicator_codes": _indicator_codes_from_records(records),
        "auto_selected": semantic,
    }, None


def _institution_context_for_message(message):
    if not any(token in message for token in ("推荐", "体检机构", "体检中心", "分院", "附近", "预约机构")):
        return None
    today = datetime.now(BUSINESS_TZ).date()
    institutions = Institution.query.filter(
        Institution.is_active.is_(True),
        Institution.operations_suspended_at.is_(None),
        Institution.organization.has(is_active=True),
    ).order_by(Institution.id).all()
    ranked = []
    for institution in institutions:
        organization_name = institution.organization.name if institution.organization else institution.name
        searchable = " ".join(filter(None, (
            organization_name,
            institution.branch_name,
            institution.district,
            institution.address,
            institution.metro_info,
        )))
        score = sum(1 for token in (
            organization_name,
            institution.branch_name,
            institution.district,
            institution.address,
        ) if token and token in message)
        # Match useful address fragments such as a district or road name.
        score += sum(1 for fragment in searchable.replace("，", " ").split() if len(fragment) >= 2 and fragment in message)
        packages = []
        for package in institution.packages:
            if not package.is_active or package.current_version_id is None:
                continue
            domain_names = [row.get("name") for row in package.to_dict().get("domains", []) if row.get("name")]
            relevance = sum(1 for token in [package.name, package.focus_area, *domain_names] if token and token in message)
            packages.append((relevance, package))
            score += relevance
        ranked.append((score, institution, packages))
    positive = [row for row in ranked if row[0] > 0]
    selected = sorted(positive or ranked, key=lambda row: (-row[0], row[1].id))[:8]
    if not selected:
        return {
            "reply": (
                "【系统机构数据】平台内当前没有启用的体检机构，暂时无法给出平台内推荐。"
                f"请稍后再试或联系平台 {PLATFORM_CONTACT['phone']}。"
            ),
            "sources": [],
        }
    lines = ["【系统机构数据】以下结果只来自 HealthDoc 当前启用机构和实时预约数据："]
    sources = []
    for _score, institution, packages in selected:
        organization_name = institution.organization.name if institution.organization else institution.name
        availability = []
        for offset in range(1, 8):
            day = today + timedelta(days=offset)
            booked = Appointment.query.filter(
                Appointment.institution_id == institution.id,
                Appointment.appointment_date == day,
                Appointment.status.in_(("pending_payment", "unfulfilled", "awaiting_report", "fulfilled")),
            ).count()
            remaining = None if institution.daily_appointment_limit is None else max(institution.daily_appointment_limit - booked, 0)
            if remaining is None or remaining > 0:
                availability.append(f"{day.isoformat()}（{'有名额' if remaining is None else f'余{remaining}人'}）")
            if len(availability) >= 2:
                break
        package_rows = [package for _relevance, package in sorted(packages, key=lambda row: (-row[0], row[1].id))[:3]]
        package_text = "；".join(f"{package.name} ¥{float(package.price):.0f}" for package in package_rows) or "暂无启用套餐"
        available_text = "、".join(availability) or "未来 7 天暂未显示空位"
        lines.append(
            f"- {organization_name}·{institution.branch_name}｜{institution.address}｜电话 {institution.consult_phone or '未填写'}"
            f"｜套餐：{package_text}｜近期：{available_text}"
        )
        sources.append({
            "type": "system_institution",
            "label": f"{organization_name}·{institution.branch_name}",
            "action_url": f"/institutions/{institution.id}",
            "booking_url": f"/appointments?institution_id={institution.id}",
        })
    lines.append("可在机构详情查看套餐，或进入预约页确认最终名额；系统内无匹配时我不会用互联网机构冒充平台数据。")
    return {"reply": "\n".join(lines), "sources": sources}


def _indicator_codes_from_records(records):
    return sorted(
        {
            item.indicator_dict.code
            for record in records
            for item in record.indicators
            if item.indicator_dict is not None
        }
    )


def _retrieve_knowledge(user, query, records=None, *, indicator_codes=None, limit=None):
    retriever = get_knowledge_retriever(current_app)
    return retriever.retrieve(
        redact_health_identity_codes(query),
        audience="authenticated"
        if user is not None and user.role == "user"
        else "public",
        indicator_codes=(
            list(indicator_codes)
            if indicator_codes is not None
            else _indicator_codes_from_records(records or [])
        ),
        limit=limit,
    )


def _knowledge_context(result):
    return redact_health_identity_codes(
        format_knowledge_context(
            result,
            max_chars=int(current_app.config.get("RAG_MAX_CONTEXT_CHARS", 12000)),
        )
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


def _format_record_context(user, records, *, indicator_codes=None):
    if not records:
        return ""

    selected_codes = set(indicator_codes or [])
    owner_label = "当前账号"
    sections = [f"档案归属：{owner_label}。共选择 {len(records)} 份档案。"]
    for index, record in enumerate(
        sorted(records, key=lambda item: (item.exam_date, item.id), reverse=True), start=1
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
            if selected_codes and definition.code not in selected_codes:
                continue
            # Resolve while the request-scoped SQLAlchemy session is alive.  The
            # resulting text is immutable and safe to consume from SSE later.
            result_status = item.resolved_result_status()
            status_label = {
                "normal": "正常", "high": "偏高", "low": "偏低",
                "positive": "阳性", "negative": "阴性", "abnormal": "异常",
            }.get(result_status)
            status_text = (
                f"；机构/系统判定 {status_label}"
                if result_status_is_displayable(result_status)
                else ""
            )
            lines.append(
                f"- {definition.name}（{definition.code}）：{item.value} {definition.unit or ''}；"
                f"参考范围 {item.reference_text or reference}{status_text}。"
            )
        sections.append("\n".join(lines))
    from app.health.routes import effective_points

    owner_id = records[0].owner_id
    daily_sections = []
    for definition in IndicatorDict.query.filter_by(allow_self_measurement=True).all():
        if selected_codes and definition.code not in selected_codes:
            continue
        points = effective_points(owner_id, definition.id)
        if points:
            recent = points[-30:]
            daily_sections.append(
                f"{definition.name}（{definition.unit or '无单位'}）：" + ", ".join(
                    f"{point['date']}={point['value']}[{point['source']}]" for point in recent
                )
            )
    if daily_sections:
        sections.append("服务端已按同日机构数据优先规则计算的每日有效数据：\n" + "\n".join(daily_sections))
    text = "\n\n".join(sections)
    if len(text) <= _MAX_RECORD_CONTEXT_CHARS:
        return redact_health_identity_codes(text)
    return redact_health_identity_codes(
        text[:_MAX_RECORD_CONTEXT_CHARS]
        + "\n…（历史档案已按相关性和长度裁剪）…"
    )


def _compact_history(history, summary):
    history = redact_health_identity_codes(history)
    summary = redact_health_identity_codes(summary)
    max_messages = int(current_app.config.get("AI_MAX_HISTORY_MESSAGES", 20))
    if len(history) < max_messages:
        return history, summary.strip(), 0
    compacted_count = 2
    return (
        history[compacted_count:],
        redact_health_identity_codes(
            merge_summary_deterministically(
                summary.strip(), history[:compacted_count]
            )
        ),
        compacted_count,
    )


def _validate_chat_request(user, payload):
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return None, _json_error("message is required", "message_required", 400)
    message = redact_health_identity_codes(message.strip())
    if len(message) > 2000:
        return None, _json_error("message is too long", "message_too_long", 400)

    summary = redact_health_identity_codes(payload.get("summary") or "")
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
    resolved_records, resolve_error = _resolve_record_context(
        user, payload, message, record_ids, record_scope
    )
    if resolve_error:
        return None, resolve_error

    model_history, updated_summary, compacted_count = _compact_history(history, summary)
    return {
        "message": message,
        "history": model_history,
        "summary": updated_summary,
        "compacted_count": compacted_count,
        "record_ids": [
            record.id for record in resolved_records["records"]
        ],
        "record_count": len(resolved_records["records"]),
        "record_scope": resolved_records["record_scope"],
        # ORM records are deliberately not retained past request validation.
        # All downstream chat/SSE code consumes the frozen context string and
        # immutable resolution metadata.
        "records": [],
        "record_context": resolved_records["record_context"],
        "record_resolution": resolved_records["resolution"],
        "next_active_record_context": resolved_records["next_context"],
        "indicator_codes": resolved_records["indicator_codes"],
        "no_record_match": resolved_records.get("no_match", False),
        "auto_selected": resolved_records["auto_selected"],
        "system_context": _institution_context_for_message(message),
        "retrieval": RetrievalResult(status="disabled"),
    }, None


def _resolve_chat_locally(user, chat_request):
    message = chat_request["message"]
    if chat_request.get("system_context") is not None:
        return {
            "result": {
                "reply": chat_request["system_context"]["reply"],
                "decision": "answer",
                "usage": {},
            },
            "source": "system_data",
            "context_sources": chat_request["system_context"]["sources"],
            "client": None,
        }
    if chat_request.get("no_record_match"):
        return {
            "result": {
                "reply": "系统内暂无符合条件的已发布体检档案。你可以调整成员或时间范围，或先在体检数据页面确认报告是否已经发布。",
                "decision": "answer",
                "usage": {},
            },
            "source": "record_resolution",
            "client": None,
        }
    if (
        user is not None
        and user.role == "user"
        and not chat_request["record_context"]
        and needs_record_selection(message)
    ):
        return {
            "action": "select_records",
            "message": "需要参考个人档案才能继续，请选择本次要引用的档案。",
            "source": "selection_rule",
            "client": None,
        }

    # A record-backed question must reach the analysis pipeline even when it
    # contains generic product words such as “趋势” or “历史指标”.  The FAQ
    # entry for the Health Trend page is useful only when no record context is
    # active; otherwise it hides the requested indicator analysis while the
    # resolver has already selected the correct reports.
    faq_answer = find_faq_answer(message)
    if faq_answer and not chat_request.get("record_context"):
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
        user,
        message,
        indicator_codes=chat_request["indicator_codes"],
    )
    retrieval = chat_request["retrieval"]
    knowledge_context = _knowledge_context(retrieval)
    client = _get_health_id_safe_ai_client()
    if user is None:
        result = answer_guest_question(
            client,
            message,
            chat_request["history"],
            chat_request["summary"],
            knowledge_context,
        )
    else:
        result = answer_authenticated_question(
            client,
            message,
            chat_request["history"],
            chat_request["summary"],
            chat_request["record_context"],
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
    payload = {
        "reply": result["reply"],
        "decision": result["decision"],
        "source": source,
        "summary": chat_request["summary"],
        "compacted_count": chat_request["compacted_count"],
        "mode": "authenticated" if user else "guest",
        "selected_record_ids": list(chat_request["record_ids"]),
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
        "context_sources": resolution.get("context_sources", []),
        "auto_selected_records": chat_request.get("auto_selected", False),
        "record_resolution": chat_request.get("record_resolution"),
        "next_active_record_context": chat_request.get(
            "next_active_record_context"
        ),
    }
    return redact_health_identity_codes(payload)


def _sse(event, payload):
    event = redact_health_identity_codes(event)
    payload = redact_health_identity_codes(payload)
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
                usage = redact_health_identity_codes(event_usage)
    finally:
        close = getattr(provider_stream, "close", None)
        if callable(close):
            close()

    content = redact_health_identity_codes("".join(content_parts).strip())
    if not content:
        raise AiProviderError(
            "AI provider returned an empty response",
            code="provider_empty_response",
            retryable=False,
        )
    return AiCompletion(content=content, usage=usage)


def _stream_model_chat_resolution(user, chat_request):
    client = _get_health_id_safe_ai_client()
    message = chat_request["message"]
    retrieval = chat_request["retrieval"]
    knowledge_context = _knowledge_context(retrieval)
    if user is None:
        messages = build_guest_messages(
            message,
            chat_request["history"],
            chat_request["summary"],
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
            chat_request["record_context"],
            knowledge_context,
        )
        completion = yield from _consume_provider_stream(
            client,
            messages,
            json_output=True,
            max_tokens=1200,
            heartbeat_message="AI 正在整理回答…",
        )
        result = parse_model_completion(completion, allowed_grounding_ids(retrieval))

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
            "message": "AI 服务尚未配置，请联系系统管理员",
            "retryable": False,
        }
    if isinstance(exc, AiProviderError):
        message = "AI 服务暂时不可用，请稍后重试"
        if exc.code == "provider_rate_limited":
            message = "AI 服务繁忙，请稍后重试"
        elif exc.code == "provider_timeout":
            message = "AI 响应超时，请重试"
        return {
            "request_id": request_id,
            "code": exc.code,
            "message": message,
            "retryable": exc.retryable,
        }
    return {
        "request_id": request_id,
        "code": "internal_error",
        "message": "AI 档案处理暂时失败，请使用相同档案重试",
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
    logger.info(
        "ai_request %s",
        json.dumps(
            redact_health_identity_codes(log_data),
            ensure_ascii=True,
            separators=(",", ":"),
        ),
    )


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

    records = (
        HealthRecord.query.options(
            joinedload(HealthRecord.owner),
            joinedload(HealthRecord.institution),
            selectinload(HealthRecord.indicators),
        )
        .filter(
            HealthRecord.owner_id == user.id,
            HealthRecord.status == "published",
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
                    "display_name": record.owner.real_name if record.owner else "未知",
                    "label": "当前账号",
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
    return redact_health_identity_codes(
        {"items": items, "owners": list(owners_by_id.values())}
    ), 200


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
        return redact_health_identity_codes({
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
        }), 200
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
                    user,
                    chat_request["message"],
                    indicator_codes=chat_request["indicator_codes"],
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
                        "record_resolution": chat_request.get("record_resolution"),
                        "next_active_record_context": chat_request.get(
                            "next_active_record_context"
                        ),
                    },
                )
                final_status = "completed"
                return

            response_payload = _chat_response_payload(user, chat_request, resolution)
            usage = response_payload.pop("usage", {})
            yield _sse(
                "status",
                {"stage": "generating", "message": "AI 已整理所需信息，正在生成回复…"},
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
                "context_sources": response_payload.get("context_sources", []),
                "auto_selected_records": response_payload.get("auto_selected_records", False),
                "selected_record_ids": response_payload.get("selected_record_ids", []),
                "record_resolution": response_payload.get("record_resolution"),
                "next_active_record_context": response_payload.get(
                    "next_active_record_context"
                ),
            }
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
                record_count=chat_request["record_count"],
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
    domain_id = payload.get("domain_id")
    if domain_id is not None:
        try: domain_id = int(domain_id)
        except (TypeError, ValueError): return _json_error("domain_id must be an integer", "invalid_domain_id", 400)
        if not db.session.get(HealthDomain, domain_id): return _json_error("health domain not found", "domain_not_found", 404)
    selected_asset_ids = payload.get("selected_asset_ids") or []
    if not isinstance(selected_asset_ids, list) or any(isinstance(value, bool) for value in selected_asset_ids):
        return _json_error("selected_asset_ids must be a list of integers", "invalid_asset_ids", 400)
    try: selected_asset_ids = list(dict.fromkeys(int(value) for value in selected_asset_ids))
    except (TypeError, ValueError): return _json_error("selected_asset_ids must be a list of integers", "invalid_asset_ids", 400)
    if selected_asset_ids and not current_app.config.get("AI_SUPPORTS_IMAGES", False):
        return _json_error("the configured AI model does not support image analysis", "image_analysis_unavailable", 409)
    if record_scope:
        records, load_error = _load_record_scope(user, record_scope)
    else:
        records, load_error = _load_selected_records(user, record_ids)
    if load_error:
        return load_error

    facts = redact_health_identity_codes(
        build_analysis_facts(user, records, domain_id=domain_id)
    )
    if domain_id is not None and not facts.get("trends") and not any(item.get("indicators") for item in facts.get("records", [])) and not facts.get("institution_text_results"):
        return _json_error("selected records contain no data in this health domain", "domain_data_unavailable", 400)
    if selected_asset_ids:
        record_ids_set = {record.id for record in records}
        assets = ReportAsset.query.filter(ReportAsset.id.in_(selected_asset_ids), ReportAsset.report_id.in_(record_ids_set)).all()
        if len(assets) != len(selected_asset_ids) or any(domain_id is not None and item.health_domain_id != domain_id for item in assets):
            return _json_error("asset is unavailable", "asset_unavailable", 404)
        facts["selected_assets"] = [{"id": item.id, "title": item.title, "modality": item.modality,
                                     "mime_type": item.mime_type, "annotation": item.annotation_text} for item in assets]
    from app.health.routes import effective_points
    owner_id = records[0].owner_id
    facts["daily_effective_indicators"] = [
        {
            "code": definition.code,
            "name": definition.name,
            "unit": definition.unit,
            "points": effective_points(owner_id, definition.id)[-90:],
        }
        for definition in IndicatorDict.query.filter_by(allow_self_measurement=True).all()
        if effective_points(owner_id, definition.id)
        and (domain_id is None or any(link.health_domain_id == domain_id for link in definition.domain_links))
    ]
    request_id = uuid.uuid4().hex
    configured_model = current_app.config.get("DEEPSEEK_MODEL")
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
            client = _get_health_id_safe_ai_client()
            messages = redact_health_identity_codes(
                build_analysis_messages(facts, _knowledge_context(retrieval))
            )
            completion = yield from _consume_provider_stream(
                client,
                messages,
                json_output=True,
                max_tokens=2200,
                heartbeat_message="AI 正在整理档案分析…",
            )
            result = parse_model_completion(
                completion, allowed_grounding_ids(retrieval)
            )
            usage = result.get("usage") or {}
            yield _sse(
                "status",
                {"stage": "generating", "message": "AI 已完成数据分析，正在整理结果…"},
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


@ai_bp.post("/trends/stream")
@jwt_required()
def analyze_trends_stream():
    user = _current_user_optional()
    if user is None or user.role != "user":
        return _json_error("只有普通用户可以分析健康趋势", "regular_user_required", 403)
    if _is_rate_limited(user):
        return _json_error("AI 请求过于频繁，请稍后再试", "rate_limited", 429, retryable=True)
    payload, payload_error = _parse_json_object()
    if payload_error:
        return payload_error
    try:
        domain_id = int(payload.get("domain_id"))
    except (TypeError, ValueError):
        return _json_error("请选择需要分析的健康方向", "invalid_domain_id", 400)
    domain = db.session.get(HealthDomain, domain_id)
    if domain is None or not domain.is_active:
        return _json_error("健康方向不存在或已停用", "domain_not_found", 404)
    owner = user
    raw_owner_id = payload.get("owner_id")
    if raw_owner_id not in {None, "", "self"}:
        try:
            owner_id = int(raw_owner_id)
        except (TypeError, ValueError):
            return _json_error("成员信息不正确", "invalid_owner_id", 400)
        if owner_id != user.id:
            return _json_error(
                "健康趋势仅分析当前有效账号；请先切换关联账号",
                "CURRENT_ACCOUNT_REQUIRED",
                403,
            )

    def parse_day(key):
        raw = payload.get(key)
        if not raw: return None
        try: return date.fromisoformat(str(raw))
        except ValueError: return False
    start_date, end_date = parse_day("start_date"), parse_day("end_date")
    if start_date is False or end_date is False or (start_date and end_date and start_date > end_date):
        return _json_error("日期范围不正确", "invalid_date_range", 400)
    source_type = str(payload.get("source_type") or "all")
    if source_type not in {"all", "self", "institution"}:
        return _json_error("趋势来源筛选不正确", "invalid_source_type", 400)
    try:
        institution_id = int(payload["institution_id"]) if payload.get("institution_id") not in {None, ""} else None
    except (TypeError, ValueError):
        return _json_error("体检机构筛选不正确", "invalid_institution_id", 400)

    from app.health.routes import effective_points
    links = IndicatorDomainLink.query.filter_by(health_domain_id=domain.id).order_by(
        IndicatorDomainLink.sort_order, IndicatorDomainLink.indicator_dict_id).all()
    raw_indicator_ids = payload.get("indicator_ids")
    selected_indicator_ids = None
    if raw_indicator_ids is not None:
        if not isinstance(raw_indicator_ids, list):
            return _json_error("指标筛选格式不正确", "invalid_indicator_ids", 400)
        try:
            selected_indicator_ids = {int(value) for value in raw_indicator_ids}
        except (TypeError, ValueError):
            return _json_error("指标筛选格式不正确", "invalid_indicator_ids", 400)
        if not selected_indicator_ids:
            return _json_error("请至少选择一个需要分析的指标", "empty_indicator_selection", 400)
        allowed_ids = {link.indicator_dict_id for link in links}
        if not selected_indicator_ids.issubset(allowed_ids):
            return _json_error("选择的指标不属于当前健康方向", "indicator_outside_domain", 400)
    indicators = []
    for link in links:
        definition = link.indicator
        if selected_indicator_ids is not None and definition.id not in selected_indicator_ids:
            continue
        points = effective_points(owner.id, definition.id, start_date, end_date,
                                  source_type=source_type, institution_id=institution_id,
                                  domain_id=domain.id)[-120:]
        if not points:
            continue
        indicators.append({
            "name": definition.name,
            "unit": definition.unit,
            "reference_low": float(definition.reference_low) if definition.reference_low is not None else None,
            "reference_high": float(definition.reference_high) if definition.reference_high is not None else None,
            "reference_context": definition.clinical_significance,
            "points": points,
        })
    if not indicators:
        return _json_error("当前筛选范围没有可分析的趋势数据", "trend_data_unavailable", 400)
    facts = redact_health_identity_codes({
        "owner_display": owner.real_name or owner.username,
        "health_domain": domain.name,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "indicators": indicators,
    })
    request_id = uuid.uuid4().hex

    def generate():
        yield _sse("meta", {"request_id": request_id, "mode": "trend_analysis",
                            "model": current_app.config.get("DEEPSEEK_MODEL")})
        yield _sse("status", {"stage": "analyzing", "message": "正在结合当前图表整理趋势…"})
        try:
            client = _get_health_id_safe_ai_client()
            completion = yield from _consume_provider_stream(
                client,
                redact_health_identity_codes(build_trend_analysis_messages(facts)),
                json_output=True,
                max_tokens=1800, heartbeat_message="AI 正在分析当前图表…")
            result = parse_model_completion(completion)
            for chunk in iter_text_chunks(result["reply"]):
                yield _sse("delta", {"text": chunk})
            done = {"request_id": request_id, "decision": result["decision"], "source": "model"}
            yield _sse("done", done)
        except (AiConfigurationError, AiProviderError) as exc:
            yield _sse("error", _stream_error_payload(exc, request_id))
        except Exception as exc:  # pragma: no cover
            current_app.logger.exception("AI trend analysis failed request_id=%s", request_id)
            yield _sse("error", _stream_error_payload(exc, request_id))
    return _sse_response(generate())


@ai_bp.get("/capabilities")
def capabilities():
    return {"image_analysis": bool(current_app.config.get("AI_SUPPORTS_IMAGES", False)),
            "domain_analysis": True, "trend_analysis": True, "analysis_persisted": False}, 200
