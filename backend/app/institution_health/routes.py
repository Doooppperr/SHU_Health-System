from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import current_app, g, request
from sqlalchemy import func

from app.extensions import db
from app.institution_health import institution_health_bp
from app.models import HealthIndicator, HealthRecord, IndicatorDict, Institution, User
from app.services.indicator_values import parse_numeric_value
from app.services.permissions import ROLE_INSTITUTION_ADMIN, roles_required


def _parse_optional_int(raw_value):
    if raw_value in {None, ""}:
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _institution():
    institution_id = g.current_user.managed_institution_id
    institution = db.session.get(Institution, institution_id) if institution_id else None
    if institution is None or not institution.is_active:
        return None, ({"message": "managed institution is unavailable"}, 403)
    return institution, None


def _log_access(action: str, institution_id: int, **details):
    event = {
        "event": "institution_health_access",
        "action": action,
        "viewer_user_id": g.current_user.id,
        "institution_id": institution_id,
        "accessed_at": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    current_app.logger.info("institution_health_access %s", json.dumps(event, ensure_ascii=False))


def _record_payload(record: HealthRecord, *, include_indicators: bool) -> dict:
    payload = {
        "id": record.id,
        "display_id": record.display_id,
        "owner_id": record.owner_id,
        "owner_display_name": record.owner.username if record.owner else None,
        "exam_date": record.exam_date.isoformat(),
        "status": record.status,
        "institution": {
            "id": record.institution.id,
            "name": record.institution.name,
            "branch_name": record.institution.branch_name,
        }
        if record.institution
        else None,
        "package": {"id": record.package.id, "name": record.package.name}
        if record.package
        else None,
        "indicator_count": len(record.indicators),
    }
    if include_indicators:
        payload["indicators"] = [item.to_dict() for item in record.indicators]
    return payload


@institution_health_bp.get("/records")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_records():
    institution, error = _institution()
    if error:
        return error
    page = max(_parse_optional_int(request.args.get("page")) or 1, 1)
    page_size = min(max(_parse_optional_int(request.args.get("page_size")) or 20, 1), 100)
    owner_keyword = (request.args.get("owner_keyword") or "").strip()
    query = HealthRecord.query.filter_by(
        institution_id=institution.id,
        status="confirmed",
    )
    if owner_keyword:
        query = query.join(User, HealthRecord.owner_id == User.id).filter(
            User.username.ilike(f"%{owner_keyword}%")
        )
    pagination = query.order_by(HealthRecord.exam_date.desc(), HealthRecord.id.desc()).paginate(
        page=page,
        per_page=page_size,
        error_out=False,
    )
    _log_access("list_records", institution.id, result_count=len(pagination.items))
    return {
        "items": [_record_payload(item, include_indicators=False) for item in pagination.items],
        "pagination": {
            "page": pagination.page,
            "page_size": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
    }, 200


@institution_health_bp.get("/records/<int:record_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def get_record(record_id: int):
    institution, error = _institution()
    if error:
        return error
    record = HealthRecord.query.filter_by(
        id=record_id,
        institution_id=institution.id,
        status="confirmed",
    ).first()
    if record is None:
        return {"message": "record not found"}, 404
    _log_access("get_record", institution.id, record_id=record.id)
    return {"item": _record_payload(record, include_indicators=True)}, 200


@institution_health_bp.get("/trends")
@roles_required(ROLE_INSTITUTION_ADMIN)
def get_trends():
    institution, error = _institution()
    if error:
        return error
    indicator_dict_id = _parse_optional_int(request.args.get("indicator_dict_id"))
    if indicator_dict_id is None:
        return {"message": "indicator_dict_id is required"}, 400
    indicator_dict = db.session.get(IndicatorDict, indicator_dict_id)
    if indicator_dict is None:
        return {"message": "indicator dict not found"}, 404
    owner_id = _parse_optional_int(request.args.get("owner_id"))
    query = (
        db.session.query(HealthIndicator, HealthRecord, User)
        .join(HealthRecord, HealthIndicator.record_id == HealthRecord.id)
        .join(User, HealthRecord.owner_id == User.id)
        .filter(
            HealthRecord.institution_id == institution.id,
            HealthRecord.status == "confirmed",
            HealthIndicator.indicator_dict_id == indicator_dict.id,
        )
    )
    if owner_id is not None:
        query = query.filter(HealthRecord.owner_id == owner_id)
    rows = query.order_by(HealthRecord.exam_date.asc(), HealthRecord.id.asc()).all()

    series = []
    numeric_values = []
    for indicator, record, owner in rows:
        parsed = parse_numeric_value(indicator.value) if indicator_dict.value_type == "numeric" else None
        numeric_value = float(parsed) if parsed is not None else None
        if numeric_value is not None:
            numeric_values.append(numeric_value)
        series.append(
            {
                "record_id": record.id,
                "record_display_id": record.display_id,
                "owner_id": owner.id,
                "owner_display_name": owner.username,
                "exam_date": record.exam_date.isoformat(),
                "value": indicator.value,
                "numeric_value": numeric_value,
                "is_abnormal": indicator.is_abnormal,
            }
        )
    _log_access("get_trends", institution.id, result_count=len(series))
    return {
        "indicator": indicator_dict.to_dict(),
        "owner_id": owner_id,
        "series": series,
        "summary": {
            "count": len(series),
            "latest": series[-1]["numeric_value"] if series else None,
            "min": min(numeric_values) if numeric_values else None,
            "max": max(numeric_values) if numeric_values else None,
        },
    }, 200
