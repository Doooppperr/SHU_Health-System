from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import g, request
from app.extensions import db
from app.health import health_bp
from app.models import (
    Appointment, AvailabilityNotificationEvent, FriendRelation, HealthDomain,
    IndicatorDict, Institution, InstitutionReport, Package, ReportIndicator,
    SelfMeasurement, User, WaitlistSubscription,
)
from app.services.permissions import ROLE_USER, roles_required


APPOINTMENT_TIMELINE_STATUS = {
    "unfulfilled": ("预约成功", "请按预约日期前往机构体检"),
    "awaiting_report": ("等待健康数据", "已确认到检，等待机构归档健康数据"),
    "fulfilled": ("已完成", "健康数据已由机构提交并归档"),
    "invalidated": ("已失效", "该预约已失效，请重新预约或联系机构"),
    "cancelled": ("已取消", "该预约已取消"),
}

BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


def report_health_data_id(report_id):
    return f"hd-i-{report_id:x}"


def self_health_data_id(owner_id, day):
    return f"hd-s-{owner_id:x}-{day.isoformat()}"


def measurement_business_day(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BUSINESS_TZ).date()


def parse_datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def as_calendar_date(value):
    """Normalize database DATE values across SQLite and openGauss dialects."""
    return value.date() if isinstance(value, datetime) else value


def requested_owner():
    raw = request.args.get("owner_id")
    if raw in {None, ""}:
        return g.current_user, None
    try: owner_id = int(raw)
    except (TypeError, ValueError): return None, ({"message": "owner_id must be an integer"}, 400)
    if owner_id == g.current_user.id: return g.current_user, None
    relation = FriendRelation.query.filter_by(user_id=g.current_user.id, friend_user_id=owner_id, auth_status=True).first()
    owner = db.session.get(User, owner_id) if relation else None
    if owner is None or owner.role != "user": return None, ({"message": "friend authorization required"}, 403)
    return owner, None


def parse_range():
    start = request.args.get("start_date")
    end = request.args.get("end_date")
    try:
        start_date = date.fromisoformat(start) if start else None
        end_date = date.fromisoformat(end) if end else None
    except ValueError:
        return None, None, ({"message": "date range must use YYYY-MM-DD"}, 400)
    if start_date and end_date and start_date > end_date:
        return None, None, ({"message": "start_date must not exceed end_date"}, 400)
    return start_date, end_date, None


@health_bp.get("/self-measurements")
@roles_required(ROLE_USER)
def list_measurements():
    query = SelfMeasurement.query.filter_by(user_id=g.current_user.id)
    indicator_id = request.args.get("indicator_dict_id", type=int)
    if indicator_id: query = query.filter_by(indicator_dict_id=indicator_id)
    start, end, error = parse_range()
    if error:
        return error
    if start:
        query = query.filter(SelfMeasurement.measured_at >= datetime.combine(start, time.min, tzinfo=BUSINESS_TZ).astimezone(timezone.utc))
    if end:
        query = query.filter(SelfMeasurement.measured_at <= datetime.combine(end, time.max, tzinfo=BUSINESS_TZ).astimezone(timezone.utc))
    page = max(request.args.get("page", default=1, type=int) or 1, 1)
    requested_size = request.args.get("page_size", type=int)
    requested_limit = request.args.get("limit", type=int)
    size = min(max(requested_size or requested_limit or 100, 1), 100)
    total = query.count()
    rows = query.order_by(SelfMeasurement.measured_at.desc(), SelfMeasurement.id.desc()).offset((page - 1) * size).limit(size).all()
    return {
        "items": [item.to_dict() for item in rows],
        "pagination": {"page": page, "page_size": size, "total": total, "pages": (total + size - 1) // size},
    }, 200


def measurement_payload(row, payload):
    definition = db.session.get(IndicatorDict, payload.get("indicator_dict_id", row.indicator_dict_id if row else None))
    if not definition or not definition.allow_self_measurement:
        return {"message": "indicator is not allowed for self measurement"}, 400
    try:
        value = Decimal(str(payload.get("value", row.value if row else None)))
    except (InvalidOperation, TypeError, ValueError):
        return {"message": "请输入有效的测量数值"}, 400
    if not value.is_finite(): return {"message": "请输入有效的测量数值"}, 400
    if value < 0: return {"message": "测量数值不能小于0"}, 400
    if value != value.quantize(Decimal("0.01")):
        return {"message": "测量数值最多保留小数点后两位"}, 400
    measured_at = parse_datetime(payload.get("measured_at", row.measured_at.isoformat() if row else None))
    if not measured_at: return {"message": "请选择有效的测量时间"}, 400
    if row is None: row = SelfMeasurement(user_id=g.current_user.id)
    row.indicator_dict_id = definition.id; row.value = value; row.measured_at = measured_at
    return row, None


@health_bp.post("/self-measurements")
@roles_required(ROLE_USER)
def create_measurement():
    row, error = measurement_payload(None, request.get_json(silent=True) or {})
    if error: return row, error
    db.session.add(row); db.session.commit(); return {"item": row.to_dict()}, 201


@health_bp.put("/self-measurements/<int:measurement_id>")
@roles_required(ROLE_USER)
def update_measurement(measurement_id):
    existing = SelfMeasurement.query.filter_by(id=measurement_id, user_id=g.current_user.id).first()
    if not existing: return {"message": "measurement not found"}, 404
    row, error = measurement_payload(existing, request.get_json(silent=True) or {})
    if error: return row, error
    db.session.commit(); return {"item": row.to_dict()}, 200


@health_bp.delete("/self-measurements/<int:measurement_id>")
@roles_required(ROLE_USER)
def delete_measurement(measurement_id):
    row = SelfMeasurement.query.filter_by(id=measurement_id, user_id=g.current_user.id).first()
    if not row: return {"message": "measurement not found"}, 404
    db.session.delete(row); db.session.commit(); return {"message": "measurement deleted"}, 200


def effective_points(owner_id, indicator_id, start_date=None, end_date=None, *, source_type="all", institution_id=None, domain_id=None):
    report_query = db.session.query(ReportIndicator, InstitutionReport).join(InstitutionReport, ReportIndicator.report_id == InstitutionReport.id).filter(
        InstitutionReport.matched_user_id == owner_id, InstitutionReport.status == "published", ReportIndicator.indicator_dict_id == indicator_id
    )
    if domain_id:
        report_query = report_query.filter(ReportIndicator.display_domain_id == domain_id)
    if institution_id:
        report_query = report_query.filter(InstitutionReport.institution_id == institution_id)
    measurement_query = SelfMeasurement.query.filter_by(user_id=owner_id, indicator_dict_id=indicator_id)
    if start_date:
        report_query = report_query.filter(InstitutionReport.exam_date >= start_date)
        measurement_query = measurement_query.filter(SelfMeasurement.measured_at >= datetime.combine(start_date, time.min, tzinfo=BUSINESS_TZ).astimezone(timezone.utc))
    if end_date:
        report_query = report_query.filter(InstitutionReport.exam_date <= end_date)
        measurement_query = measurement_query.filter(SelfMeasurement.measured_at <= datetime.combine(end_date, time.max, tzinfo=BUSINESS_TZ).astimezone(timezone.utc))
    points = {}
    if source_type in {"all", "self"} and not institution_id:
        for row in measurement_query.order_by(SelfMeasurement.measured_at.asc(), SelfMeasurement.id.asc()).all():
            day = measurement_business_day(row.measured_at)
            points[day] = {"date": day.isoformat(), "value": float(row.value), "source": "self_measurement", "measurement_id": row.id, "measured_at": row.measured_at.isoformat()}
    if source_type == "self":
        return [points[key] for key in sorted(points)]
    for indicator, report in report_query.order_by(InstitutionReport.exam_date.asc(), InstitutionReport.published_at.asc(), InstitutionReport.id.asc()).all():
        try: value = float(indicator.value)
        except (TypeError, ValueError): continue
        report_day = as_calendar_date(report.exam_date)
        points[report_day] = {"date": report_day.isoformat(), "value": value, "source": "institution_report", "report_id": report.id, "institution": report.institution.name if report.institution else None}
    return [points[key] for key in sorted(points)]


@health_bp.get("/health/trends/<int:indicator_id>")
@roles_required(ROLE_USER)
def trend(indicator_id):
    owner, error = requested_owner()
    if error: return error
    definition = db.session.get(IndicatorDict, indicator_id)
    if not definition: return {"message": "indicator not found"}, 404
    start, end, range_error = parse_range()
    if range_error: return range_error
    points = effective_points(owner.id, indicator_id, start, end)
    values = [point["value"] for point in points]
    return {"owner": owner.friend_identity_dict(), "indicator": definition.to_dict(), "points": points, "summary": {"latest": values[-1] if values else None, "highest": max(values) if values else None, "lowest": min(values) if values else None, "count": len(values)}}, 200


def _domain_rows(domain_ids):
    if not domain_ids:
        return []
    return [
        row.to_dict()
        for row in HealthDomain.query.filter(HealthDomain.id.in_(domain_ids)).order_by(
            HealthDomain.sort_order, HealthDomain.id
        ).all()
    ]


def _appointment_domains(row):
    if row.report and row.report.status == "published":
        ids = {item.display_domain_id for item in row.report.indicators if item.display_domain_id}
        ids.update(item.health_domain_id for item in row.report.text_results)
        ids.update(item.health_domain_id for item in row.report.assets)
        return _domain_rows(ids)
    if row.booking_group and row.booking_group.domain_snapshot:
        return row.booking_group.domain_snapshot
    if row.package_version:
        return [item.domain.to_dict() for item in row.package_version.domains if item.domain]
    return []


def _appointment_history(row):
    history = [event.to_dict() for event in row.events]
    if history:
        return history
    history = [{"type": "booked", "status": "unfulfilled", "message": "预约成功",
                "occurred_at": row.created_at.isoformat() if row.created_at else None}]
    if row.attended_at:
        history.append({"type": "attended", "status": "awaiting_report", "message": "机构确认到检", "occurred_at": row.attended_at.isoformat()})
    if row.fulfilled_at:
        history.append({"type": "archived", "status": "fulfilled", "message": "健康数据已归档", "occurred_at": row.fulfilled_at.isoformat()})
    if row.cancelled_at:
        history.append({"type": "cancelled", "status": "cancelled", "message": "预约已取消", "occurred_at": row.cancelled_at.isoformat()})
    if row.invalidated_at:
        history.append({"type": "invalidated", "status": "invalidated", "message": "预约已失效", "occurred_at": row.invalidated_at.isoformat()})
    return history


def _appointment_timeline_item(row, *, include_participant_name=True):
    appointment_day = as_calendar_date(row.appointment_date)
    status_label, status_message = APPOINTMENT_TIMELINE_STATUS[row.status]
    health_data_id = (
        report_health_data_id(row.report.id)
        if row.status == "fulfilled" and row.report and row.report.status == "published"
        else None
    )
    institution = (
        {"id": row.institution.id, "name": row.institution.name, "branch_name": row.institution.branch_name}
        if row.institution else None
    )
    participant_name = (
        row.user_name_snapshot or (row.user.real_name if row.user else None) or "本人"
        if include_participant_name else "已授权亲友"
    )
    domains = _appointment_domains(row)
    payload = row.to_dict()
    return {
        "record_type": "exam",
        "type": "appointment",
        "record_key": f"exam-{row.id}",
        "occurred_at": datetime.combine(appointment_day, time.min, tzinfo=BUSINESS_TZ).isoformat(),
        "business_date": appointment_day.isoformat(),
        "title": f"{row.package_name_snapshot or '体检服务'} · {status_label}",
        "source": {"type": "institution", **institution} if institution else None,
        "institution": institution,
        "domains": domains,
        "status": {"code": row.status, "label": status_label, "message": status_message},
        "summary": {
            "package_name": row.package_name_snapshot,
            "participant_name": participant_name,
            "domain_count": len(domains),
        },
        "health_data_id": health_data_id,
        "events": _appointment_history(row),
        # Compatibility for older clients during the local refactor.
        "item": {**payload, "status_label": status_label, "status_message": status_message,
                 "health_data_id": health_data_id, "domains": domains},
        "_sort_id": row.id,
    }


def _self_timeline_item(owner, day, rows):
    latest_by_indicator = {}
    for row in sorted(rows, key=lambda item: (item.measured_at, item.id)):
        latest_by_indicator[row.indicator_dict_id] = row
    highlights = []
    for row in sorted(latest_by_indicator.values(), key=lambda item: (item.measured_at, item.id), reverse=True)[:3]:
        highlights.append({
            "indicator_id": row.indicator_dict_id,
            "name": row.indicator_dict.name,
            "value": float(row.value),
            "unit": row.indicator_dict.unit,
            "measured_at": row.measured_at.isoformat(),
        })
    domain_ids = {
        link.health_domain_id
        for row in rows
        for link in row.indicator_dict.domain_links
        if link.is_primary
    }
    health_data_id = self_health_data_id(owner.id, day)
    latest = max(rows, key=lambda item: (item.measured_at, item.id))
    return {
        "record_type": "self",
        "type": "self_measurement",
        "record_key": health_data_id,
        "occurred_at": latest.measured_at.isoformat(),
        "business_date": day.isoformat(),
        "title": "当日个人健康记录",
        "source": {"type": "self", "id": None, "name": "本人记录", "branch_name": None},
        "institution": None,
        "domains": _domain_rows(domain_ids),
        "status": None,
        "summary": {"indicator_count": len(rows), "indicator_type_count": len(latest_by_indicator),
                    "highlights": highlights},
        "health_data_id": health_data_id,
        "events": [],
        "item": {"id": health_data_id, "indicator_count": len(rows), "highlights": highlights},
        "_sort_id": latest.id,
    }


def _timeline_records(
    owner, start=None, end=None, record_type="all", institution_id=None, status=None,
    *, include_participant_name=True,
):
    items = []
    if record_type in {"all", "exam"}:
        query = Appointment.query.filter_by(user_id=owner.id)
        if start:
            query = query.filter(Appointment.appointment_date >= start)
        if end:
            query = query.filter(Appointment.appointment_date <= end)
        if institution_id:
            query = query.filter(Appointment.institution_id == institution_id)
        if status:
            query = query.filter(Appointment.status == status)
        items.extend(
            _appointment_timeline_item(row, include_participant_name=include_participant_name)
            for row in query.all()
        )
    if record_type in {"all", "self"} and not institution_id and not status:
        measurements = SelfMeasurement.query.filter_by(user_id=owner.id)
        if start:
            measurements = measurements.filter(
                SelfMeasurement.measured_at >= datetime.combine(start, time.min, tzinfo=BUSINESS_TZ).astimezone(timezone.utc)
            )
        if end:
            measurements = measurements.filter(
                SelfMeasurement.measured_at <= datetime.combine(end, time.max, tzinfo=BUSINESS_TZ).astimezone(timezone.utc)
            )
        grouped = defaultdict(list)
        for row in measurements.order_by(SelfMeasurement.measured_at, SelfMeasurement.id).all():
            day = measurement_business_day(row.measured_at)
            if (not start or day >= start) and (not end or day <= end):
                grouped[day].append(row)
        items.extend(_self_timeline_item(owner, day, rows) for day, rows in grouped.items())
    items.sort(key=lambda item: (item["business_date"], item["occurred_at"], item["_sort_id"]), reverse=True)
    for item in items:
        item.pop("_sort_id", None)
    return items


def _waitlist_summary(row):
    if not row:
        return None
    institution = db.session.get(Institution, row.institution_id)
    package = db.session.get(Package, row.package_id)
    event = AvailabilityNotificationEvent.query.filter_by(subscription_id=row.id).order_by(
        AvailabilityNotificationEvent.created_at.desc(), AvailabilityNotificationEvent.id.desc()
    ).first()
    return {
        **row.to_dict(),
        "institution": {"id": institution.id, "name": institution.name, "branch_name": institution.branch_name} if institution else None,
        "package": {"id": package.id, "name": package.name} if package else None,
        "status_label": {"active": "等待可预约提醒", "closed": "已完成预约", "cancelled": "已取消", "invalid": "已失效"}.get(row.status, row.status),
        "last_notification": ({"sent_at": event.created_at.isoformat(), "remaining": event.remaining_snapshot}
                              if event else None),
    }


@health_bp.get("/health/dashboard")
@roles_required(ROLE_USER)
def health_dashboard():
    owner = g.current_user
    today = datetime.now(BUSINESS_TZ).date()
    today_rows = [
        row for row in SelfMeasurement.query.filter_by(user_id=owner.id).order_by(
            SelfMeasurement.measured_at, SelfMeasurement.id
        ).all() if measurement_business_day(row.measured_at) == today
    ]
    today_record = _self_timeline_item(owner, today, today_rows) if today_rows else None
    if today_record:
        today_record.pop("_sort_id", None)
    next_appointment = Appointment.query.filter(
        Appointment.user_id == owner.id,
        Appointment.status == "unfulfilled",
        Appointment.appointment_date >= today,
    ).order_by(Appointment.appointment_date, Appointment.id).first()
    latest_report = InstitutionReport.query.filter_by(matched_user_id=owner.id, status="published").order_by(
        InstitutionReport.exam_date.desc(), InstitutionReport.id.desc()
    ).first()
    latest_health_data = None
    if latest_report:
        latest_health_data = {
            "health_data_id": report_health_data_id(latest_report.id),
            "business_date": as_calendar_date(latest_report.exam_date).isoformat(),
            "source": {"id": latest_report.institution_id, "name": latest_report.institution.name,
                       "branch_name": latest_report.institution.branch_name} if latest_report.institution else None,
            "package": {"id": latest_report.package_id, "name": latest_report.package.name} if latest_report.package else None,
            "domains": _appointment_domains(latest_report.appointment) if latest_report.appointment else [],
            "indicator_count": len(latest_report.indicators),
            "text_result_count": len(latest_report.text_results),
            "asset_count": len(latest_report.assets),
        }
    active_waitlist = WaitlistSubscription.query.filter_by(
        subscriber_user_id=owner.id, status="active"
    ).order_by(WaitlistSubscription.created_at.desc()).first()
    recent = _timeline_records(owner)[:6]
    next_appointment_payload = _appointment_timeline_item(next_appointment) if next_appointment else None
    if next_appointment_payload:
        next_appointment_payload.pop("_sort_id", None)
    return {
        "today": today.isoformat(),
        "today_measurements": today_record,
        "next_appointment": next_appointment_payload,
        "latest_health_data": latest_health_data,
        "active_waitlist": _waitlist_summary(active_waitlist),
        "recent_timeline": recent,
    }, 200


@health_bp.get("/health/timeline")
@roles_required(ROLE_USER)
def timeline():
    owner, error = requested_owner()
    if error: return error
    start, end, range_error = parse_range()
    if range_error: return range_error
    record_type = (request.args.get("record_type") or "all").strip().lower()
    if record_type not in {"all", "exam", "self"}:
        return {"message": "record_type must be all, exam or self"}, 400
    institution_id = request.args.get("institution_id", type=int)
    status = (request.args.get("status") or "").strip()
    page = max(request.args.get("page", default=1, type=int) or 1, 1)
    page_size = 30 if request.args.get("page_size", default=15, type=int) == 30 else 15
    records = _timeline_records(
        owner, start, end, record_type, institution_id, status,
        include_participant_name=owner.id == g.current_user.id,
    )
    total = len(records)
    items = records[(page - 1) * page_size:page * page_size]
    return {"owner": owner.friend_identity_dict(), "items": items,
            "pagination": {"page": page, "page_size": page_size, "total": total,
                           "pages": (total + page_size - 1) // page_size}}, 200
