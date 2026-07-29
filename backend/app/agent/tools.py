from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask import current_app
from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.agent.crypto import encrypt_json
from app.agent.schemas import (
    AppointmentStatusArgs,
    AvailabilityArgs,
    BookingDraftArgs,
    CancellationDraftArgs,
    ComparePackagesArgs,
    IndicatorTrendArgs,
    ListReportsArgs,
    ReportFactsArgs,
    SearchInstitutionsArgs,
    SupportHandoffDraftArgs,
    WaitlistDraftArgs,
)
from app.extensions import db
from app.models import (
    AgentPendingAction,
    AgentToolEvent,
    Appointment,
    BookingGroup,
    FriendRelation,
    HealthRecord,
    IndicatorDict,
    Institution,
    Package,
    ReportIndicator,
    User,
)


READ_TOOLS = {
    "list_reports": ListReportsArgs,
    "get_report_facts": ReportFactsArgs,
    "compute_indicator_trend": IndicatorTrendArgs,
    "search_institutions": SearchInstitutionsArgs,
    "compare_packages": ComparePackagesArgs,
    "check_availability": AvailabilityArgs,
    "get_appointment_status": AppointmentStatusArgs,
}
DRAFT_TOOLS = {
    "create_booking_draft": BookingDraftArgs,
    "create_cancellation_draft": CancellationDraftArgs,
    "create_waitlist_draft": WaitlistDraftArgs,
    "create_support_handoff_draft": SupportHandoffDraftArgs,
}
TOOL_MODELS = {**READ_TOOLS, **DRAFT_TOOLS}

TOOL_DESCRIPTIONS = {
    "list_reports": "列出当前用户本人或已授权亲友的已发布体检报告。",
    "get_report_facts": "读取已授权报告中的结构化指标事实。",
    "compute_indicator_trend": "用确定性计算返回某一指标的历次变化。",
    "search_institutions": "按名称、区域或地址搜索可用体检机构。",
    "compare_packages": (
        "比较指定体检套餐，或按 institution_id 列出机构套餐；"
        "用户要求最便宜套餐时使用 sort_by=price_asc。"
    ),
    "check_availability": "检查机构指定日期是否有足够预约名额。",
    "get_appointment_status": "查询当前用户创建的预约组状态。",
    "create_booking_draft": "生成预约草稿；不会直接预约，必须由用户确认。",
    "create_cancellation_draft": "生成整组取消草稿；不会直接取消，必须由用户确认。",
    "create_waitlist_draft": "生成候补提醒草稿；必须由用户确认。",
    "create_support_handoff_draft": "生成人工客服工单草稿；必须由用户确认。",
}


def tool_definitions(*, allow_drafts: bool) -> list[dict]:
    names = list(READ_TOOLS)
    if allow_drafts:
        names.extend(DRAFT_TOOLS)
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": TOOL_DESCRIPTIONS[name],
                "strict": True,
                "parameters": TOOL_MODELS[name].model_json_schema(),
            },
        }
        for name in names
    ]


def _authorized_owner_ids(user: User) -> set[int]:
    result = {user.id}
    rows = FriendRelation.query.filter_by(user_id=user.id, auth_status=True).all()
    result.update(row.friend_user_id for row in rows)
    return result


def _owner_id(user: User, requested: int | None) -> int:
    owner_id = requested or user.id
    if owner_id not in _authorized_owner_ids(user):
        raise PermissionError("当前账号没有查看该受检者健康档案的权限")
    return owner_id


def _read_list_reports(user, args: ListReportsArgs):
    owner_id = _owner_id(user, args.owner_id)
    rows = (
        HealthRecord.query.options(joinedload(HealthRecord.institution))
        .filter_by(matched_user_id=owner_id, status="published")
        .order_by(HealthRecord.exam_date.desc(), HealthRecord.id.desc())
        .limit(args.limit)
        .all()
    )
    return {
        "owner_id": owner_id,
        "reports": [
            {
                "id": row.id,
                "exam_date": row.exam_date.isoformat(),
                "institution": row.institution.name if row.institution else None,
                "indicator_count": len(row.indicators),
            }
            for row in rows
        ],
    }


def _read_report_facts(user, args: ReportFactsArgs):
    allowed = _authorized_owner_ids(user)
    rows = (
        HealthRecord.query.options(
            joinedload(HealthRecord.indicators).joinedload(
                ReportIndicator.indicator_dict
            )
        )
        .filter(
            HealthRecord.id.in_(args.report_ids),
            HealthRecord.matched_user_id.in_(allowed),
            HealthRecord.status == "published",
        )
        .all()
    )
    if len(rows) != len(set(args.report_ids)):
        raise PermissionError("部分报告不存在或当前账号没有查看权限")
    codes = {value.strip().upper() for value in args.indicator_codes if value.strip()}
    reports = []
    for row in sorted(rows, key=lambda item: (item.exam_date, item.id)):
        indicators = []
        for item in row.indicators:
            definition = item.indicator_dict
            if definition is None or (codes and definition.code.upper() not in codes):
                continue
            indicators.append(
                {
                    "code": definition.code,
                    "name": definition.name,
                    "value": item.value,
                    "unit": item.normalized_unit or definition.unit,
                    "result_status": item.resolved_result_status(),
                    "reference": item.reference_text,
                }
            )
        reports.append(
            {"id": row.id, "exam_date": row.exam_date.isoformat(), "indicators": indicators}
        )
    return {"reports": reports}


def _read_indicator_trend(user, args: IndicatorTrendArgs):
    owner_id = _owner_id(user, args.owner_id)
    definition = IndicatorDict.query.filter(
        db.func.upper(IndicatorDict.code) == args.indicator_code.strip().upper()
    ).first()
    if definition is None:
        return {"owner_id": owner_id, "indicator_code": args.indicator_code, "points": []}
    reports = (
        HealthRecord.query.filter_by(matched_user_id=owner_id, status="published")
        .order_by(HealthRecord.exam_date.desc(), HealthRecord.id.desc())
        .limit(args.limit * 3)
        .all()
    )
    points = []
    for report in reports:
        row = next(
            (item for item in report.indicators if item.indicator_dict_id == definition.id),
            None,
        )
        if row is None:
            continue
        try:
            numeric = Decimal(str(row.value))
        except (InvalidOperation, ValueError):
            continue
        points.append(
            {
                "date": report.exam_date.isoformat(),
                "value": float(numeric),
                "unit": row.normalized_unit or definition.unit,
                "status": row.resolved_result_status(),
            }
        )
    points = list(reversed(points[: args.limit]))
    changes = []
    for previous, current in zip(points, points[1:]):
        delta = current["value"] - previous["value"]
        percent = None if previous["value"] == 0 else round(delta / previous["value"] * 100, 2)
        changes.append(
            {
                "from": previous["date"],
                "to": current["date"],
                "delta": round(delta, 4),
                "percent": percent,
            }
        )
    return {
        "owner_id": owner_id,
        "indicator": {"code": definition.code, "name": definition.name},
        "points": points,
        "changes": changes,
    }


def _read_institutions(_user, args: SearchInstitutionsArgs):
    query = Institution.query.filter_by(is_active=True)
    if args.district:
        query = query.filter(Institution.district == args.district)
    keyword = args.keyword.strip()
    if keyword:
        term = f"%{keyword}%"
        query = query.filter(
            or_(
                Institution.name.ilike(term),
                Institution.branch_name.ilike(term),
                Institution.address.ilike(term),
                Institution.district.ilike(term),
            )
        )
    rows = query.order_by(Institution.id.asc()).limit(args.limit).all()
    return {
        "institutions": [
            {
                "id": row.id,
                "name": row.organization.name if row.organization else row.name,
                "branch_name": row.branch_name,
                "district": row.district,
                "address": row.address,
                "metro_info": row.metro_info,
                "consult_phone": row.consult_phone,
            }
            for row in rows
        ]
    }


def _read_packages(_user, args: ComparePackagesArgs):
    if not args.package_ids and args.institution_id is None:
        raise ValueError("package_ids 和 institution_id 至少提供一个")
    query = Package.query.filter(Package.is_active.is_(True))
    if args.package_ids:
        query = query.filter(Package.id.in_(args.package_ids))
    if args.institution_id is not None:
        query = query.filter(Package.institution_id == args.institution_id)
    rows = query.all()
    if args.sort_by == "price_asc":
        rows.sort(key=lambda item: (item.price, item.id))
    elif args.sort_by == "price_desc":
        rows.sort(key=lambda item: (-item.price, item.id))
    elif args.package_ids:
        positions = {package_id: index for index, package_id in enumerate(args.package_ids)}
        rows.sort(key=lambda item: positions.get(item.id, len(positions)))
    else:
        rows.sort(key=lambda item: item.id)
    rows = rows[: args.limit]
    packages = [
        {
            "id": row.id,
            "institution_id": row.institution_id,
            "institution": row.institution.name if row.institution else None,
            "name": row.name,
            "price": float(row.price),
            "focus_area": row.focus_area,
            "gender_scope": row.gender_scope,
            "audience": row.audience,
            "description": row.description,
            "booking_notice": row.booking_notice,
            "domains": row.to_dict().get("domains", []),
        }
        for row in rows
    ]
    cheapest = min(packages, key=lambda item: (item["price"], item["id"])) if packages else None
    return {
        "packages": packages,
        "selection_hints": {
            "cheapest_package_id": cheapest["id"] if cheapest else None,
            "cheapest_package_name": cheapest["name"] if cheapest else None,
        },
    }


def _read_availability(_user, args: AvailabilityArgs):
    institution = Institution.query.filter_by(id=args.institution_id, is_active=True).first()
    if institution is None:
        raise LookupError("没有找到可预约机构")
    active = ("unfulfilled", "awaiting_report", "fulfilled")
    booked = Appointment.query.filter(
        Appointment.institution_id == institution.id,
        Appointment.appointment_date == args.appointment_date,
        Appointment.status.in_(active),
    ).count()
    capacity = institution.daily_appointment_limit
    remaining = None if capacity is None else max(capacity - booked, 0)
    return {
        "institution_id": institution.id,
        "appointment_date": args.appointment_date.isoformat(),
        "capacity": capacity,
        "booked": booked,
        "remaining": remaining,
        "party_size": args.party_size,
        "available": remaining is None or remaining >= args.party_size,
    }


def _read_appointments(user, args: AppointmentStatusArgs):
    query = BookingGroup.query.filter_by(booked_by_user_id=user.id)
    if args.group_id is not None:
        query = query.filter_by(id=args.group_id)
    rows = query.order_by(BookingGroup.created_at.desc()).limit(args.limit).all()
    return {
        "booking_groups": [
            {
                "id": row.id,
                "group_code": row.group_code,
                "appointment_date": row.appointment_date.isoformat(),
                "institution_id": row.institution_id,
                "package_name": row.package_name_snapshot,
                "party_size": row.party_size,
                "statuses": [item.status for item in row.appointments],
                "can_cancel": bool(row.appointments)
                and all(item.status == "unfulfilled" for item in row.appointments),
            }
            for row in rows
        ]
    }


READ_HANDLERS = {
    "list_reports": _read_list_reports,
    "get_report_facts": _read_report_facts,
    "compute_indicator_trend": _read_indicator_trend,
    "search_institutions": _read_institutions,
    "compare_packages": _read_packages,
    "check_availability": _read_availability,
    "get_appointment_status": _read_appointments,
}


def _draft_summary(name: str, args, *, normalized_payload=None) -> dict:
    payload = normalized_payload or args.model_dump(mode="json")
    if name == "create_booking_draft":
        institution = db.session.get(Institution, payload["institution_id"])
        package = db.session.get(Package, payload["package_id"])
        intakes = payload["participant_intakes"]
        intake_summary = "；".join(
            (
                f"{float(item['height_cm']):g} cm / "
                f"{float(item['weight_kg']):g} kg"
            )
            for item in intakes
        )
        return {
            "title": "确认提交体检预约",
            "体检机构": institution.name if institution else f"机构 {payload['institution_id']}",
            "体检套餐": package.name if package else f"套餐 {payload['package_id']}",
            "体检日期": payload["appointment_date"],
            "预约人数": len(payload["participant_user_ids"] or []),
            "身高/体重": intake_summary,
        }
    if name == "create_cancellation_draft":
        return {"title": "确认取消整个预约组", "预约组编号": payload["group_id"]}
    if name == "create_waitlist_draft":
        institution = db.session.get(Institution, payload["institution_id"])
        package = db.session.get(Package, payload["package_id"])
        return {
            "title": "确认订阅空位提醒",
            "体检机构": institution.name if institution else f"机构 {payload['institution_id']}",
            "体检套餐": package.name if package else f"套餐 {payload['package_id']}",
            "体检日期": payload["appointment_date"],
        }
    return {
        "title": "确认创建人工客服工单",
        "问题类型": payload["category"],
        "优先级": payload["priority"],
        "问题摘要": payload["summary"],
    }


def _create_draft(name, args, *, user, thread_id, run_id):
    if not current_app.config.get("AGENT_WRITE_ENABLED"):
        raise PermissionError("Agent 写操作当前未启用")
    action_type = name.removeprefix("create_").removesuffix("_draft")
    action_id = str(uuid.uuid4())
    payload = args.model_dump(mode="json")
    if name == "create_booking_draft":
        if not payload["participant_user_ids"]:
            payload["participant_user_ids"] = [user.id]
        for intake in payload["participant_intakes"]:
            if intake.get("user_id") is None:
                intake["user_id"] = user.id
        participant_ids = payload["participant_user_ids"]
        intake_ids = [intake["user_id"] for intake in payload["participant_intakes"]]
        if (
            len(set(participant_ids)) != len(participant_ids)
            or len(set(intake_ids)) != len(intake_ids)
            or set(intake_ids) != set(participant_ids)
        ):
            raise ValueError(
                "预约草稿必须包含每位受检者且仅包含一份身高体重资料"
            )
    now = datetime.now(timezone.utc)
    AgentPendingAction.query.filter_by(
        thread_id=thread_id,
        user_id=user.id,
        action_type=action_type,
        status="pending",
    ).update(
        {"status": "expired", "decided_at": now},
        synchronize_session=False,
    )
    expires_at = now + timedelta(
        seconds=int(current_app.config.get("AGENT_ACTION_TTL_SECONDS", 600))
    )
    action = AgentPendingAction(
        id=action_id,
        thread_id=thread_id,
        run_id=run_id,
        user_id=user.id,
        action_type=action_type,
        encrypted_payload=encrypt_json(payload, purpose=f"agent-action:{action_id}"),
        summary=_draft_summary(name, args, normalized_payload=payload),
        expires_at=expires_at,
    )
    db.session.add(action)
    db.session.flush()
    return {
        "approval_required": True,
        "action_id": action.id,
        "action_type": action.action_type,
        "summary": action.summary,
        "expires_at": action.expires_at.isoformat(),
    }


def execute_tool(name, raw_arguments, *, user, thread_id, run_id):
    model = TOOL_MODELS.get(name)
    if model is None:
        raise LookupError(f"不允许调用工具：{name}")
    try:
        if isinstance(raw_arguments, str):
            raw_arguments = json.loads(raw_arguments or "{}")
        args = model.model_validate(raw_arguments)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ValueError(f"工具参数无效：{name}") from exc

    started = time.monotonic()
    event = AgentToolEvent(
        run_id=run_id,
        tool_name=name,
        status="started",
        redacted_input={"fields": sorted(args.model_dump(mode="json").keys())},
    )
    db.session.add(event)
    db.session.flush()
    try:
        if name in READ_HANDLERS:
            result = READ_HANDLERS[name](user, args)
        else:
            result = _create_draft(
                name, args, user=user, thread_id=thread_id, run_id=run_id
            )
        event.status = "completed"
        event.redacted_output = {
            "keys": sorted(result.keys()),
            "item_count": sum(len(value) for value in result.values() if isinstance(value, list)),
        }
        event.duration_ms = int((time.monotonic() - started) * 1000)
        db.session.commit()
        return result
    except PermissionError:
        event.status = "denied"
        event.duration_ms = int((time.monotonic() - started) * 1000)
        db.session.commit()
        raise
    except Exception:
        event.status = "failed"
        event.duration_ms = int((time.monotonic() - started) * 1000)
        db.session.commit()
        raise
