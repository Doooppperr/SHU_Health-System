from collections import defaultdict
from datetime import date, datetime, time, timezone

from flask import g, request
from app.extensions import db
from app.health import health_bp
from app.models import (
    FriendRelation, IndicatorDict, InstitutionReport, ReportIndicator, SelfMeasurement, User,
)
from app.services.permissions import ROLE_USER, roles_required


def parse_datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


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
    return start_date, end_date, None


@health_bp.get("/self-measurements")
@roles_required(ROLE_USER)
def list_measurements():
    query = SelfMeasurement.query.filter_by(user_id=g.current_user.id)
    indicator_id = request.args.get("indicator_dict_id", type=int)
    if indicator_id: query = query.filter_by(indicator_dict_id=indicator_id)
    return {"items": [item.to_dict() for item in query.order_by(SelfMeasurement.measured_at.desc(), SelfMeasurement.id.desc()).all()]}, 200


def measurement_payload(row, payload):
    definition = db.session.get(IndicatorDict, payload.get("indicator_dict_id", row.indicator_dict_id if row else None))
    if not definition or not definition.allow_self_measurement:
        return {"message": "indicator is not allowed for self measurement"}, 400
    try:
        value = float(payload.get("value", row.value if row else None))
    except (TypeError, ValueError):
        return {"message": "value must be numeric"}, 400
    if value < 0: return {"message": "value must be non-negative"}, 400
    measured_at = parse_datetime(payload.get("measured_at", row.measured_at.isoformat() if row else None))
    if not measured_at: return {"message": "measured_at must be an ISO datetime"}, 400
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


def effective_points(owner_id, indicator_id, start_date=None, end_date=None):
    report_query = db.session.query(ReportIndicator, InstitutionReport).join(InstitutionReport, ReportIndicator.report_id == InstitutionReport.id).filter(
        InstitutionReport.matched_user_id == owner_id, InstitutionReport.status == "published", ReportIndicator.indicator_dict_id == indicator_id
    )
    measurement_query = SelfMeasurement.query.filter_by(user_id=owner_id, indicator_dict_id=indicator_id)
    if start_date:
        report_query = report_query.filter(InstitutionReport.exam_date >= start_date)
        measurement_query = measurement_query.filter(SelfMeasurement.measured_at >= datetime.combine(start_date, time.min, tzinfo=timezone.utc))
    if end_date:
        report_query = report_query.filter(InstitutionReport.exam_date <= end_date)
        measurement_query = measurement_query.filter(SelfMeasurement.measured_at <= datetime.combine(end_date, time.max, tzinfo=timezone.utc))
    points = {}
    for row in measurement_query.order_by(SelfMeasurement.measured_at.asc(), SelfMeasurement.id.asc()).all():
        day = row.measured_at.date()
        points[day] = {"date": day.isoformat(), "value": float(row.value), "source": "self_measurement", "measurement_id": row.id, "measured_at": row.measured_at.isoformat()}
    for indicator, report in report_query.order_by(InstitutionReport.exam_date.asc(), InstitutionReport.id.asc()).all():
        try: value = float(indicator.value)
        except (TypeError, ValueError): continue
        points[report.exam_date] = {"date": report.exam_date.isoformat(), "value": value, "source": "institution_report", "report_id": report.id, "institution": report.institution.name if report.institution else None}
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


@health_bp.get("/health/timeline")
@roles_required(ROLE_USER)
def timeline():
    owner, error = requested_owner()
    if error: return error
    events = []
    friend_view = owner.id != g.current_user.id
    for row in SelfMeasurement.query.filter_by(user_id=owner.id).all():
        payload = row.to_dict()
        if friend_view: payload.pop("user_id", None)
        events.append({"type": "self_measurement", "occurred_at": row.measured_at.isoformat(), "title": f"自测{row.indicator_dict.name}", "item": payload})
    for row in InstitutionReport.query.filter(InstitutionReport.matched_user_id == owner.id, InstitutionReport.status.in_(["published", "withdrawn"])).all():
        suffix = "机构已提交" if row.status == "published" else "机构已撤下"
        occurred = row.published_at if row.status == "published" else row.withdrawn_at
        payload = row.to_dict(include_indicators=row.status == "published", user_view=True)
        if friend_view:
            payload.pop("subject_name_snapshot", None)
            payload.pop("matched_user_id", None)
        events.append({"type": "institution_report" if row.status == "published" else "report_withdrawn", "occurred_at": (occurred or datetime.combine(row.exam_date, time.min)).isoformat(), "title": f"{row.institution.name} 体检报告 · {suffix}", "item": payload})
    events.sort(key=lambda item: item["occurred_at"], reverse=True)
    return {"owner": owner.friend_identity_dict(), "items": events}, 200
