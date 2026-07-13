from decimal import Decimal

from flask import request
from flask_jwt_extended import get_jwt_identity, jwt_required, verify_jwt_in_request

from app.extensions import db
from app.models import FriendRelation, HealthIndicator, HealthRecord, IndicatorDict, User
from app.services.indicator_values import parse_numeric_value
from app.services.permissions import ROLE_USER, get_current_user, role_error
from app.trends import trends_bp


@trends_bp.before_request
def _require_regular_user_for_trends():
    verify_jwt_in_request()
    return role_error(get_current_user(), ROLE_USER)


def _current_user_id() -> int:
    return int(get_jwt_identity())


def _parse_optional_int(raw_value):
    if raw_value is None or raw_value == "":
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _can_manage_owner(user_id: int, owner_id: int) -> bool:
    if user_id == owner_id:
        return True

    relation = FriendRelation.query.filter_by(
        user_id=user_id,
        friend_user_id=owner_id,
        auth_status=True,
    ).first()
    return relation is not None


def _to_float(value: Decimal | None):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@trends_bp.get("/indicators/<int:indicator_dict_id>")
@jwt_required()
def get_indicator_trend(indicator_dict_id: int):
    user_id = _current_user_id()

    owner_id = _parse_optional_int(request.args.get("owner_id")) or user_id
    owner = db.session.get(User, owner_id)
    if owner is None:
        return {"message": "owner user not found"}, 404

    if not _can_manage_owner(user_id, owner_id):
        return {"message": "friend authorization required"}, 403

    indicator_dict = db.session.get(IndicatorDict, indicator_dict_id)
    if indicator_dict is None:
        return {"message": "indicator dict not found"}, 404

    rows = (
        db.session.query(HealthIndicator, HealthRecord)
        .join(HealthRecord, HealthIndicator.record_id == HealthRecord.id)
        .filter(
            HealthRecord.owner_id == owner_id,
            HealthIndicator.indicator_dict_id == indicator_dict_id,
        )
        .order_by(HealthRecord.exam_date.asc(), HealthRecord.id.asc(), HealthIndicator.id.asc())
        .all()
    )

    points = []
    numeric_values = []
    for indicator_row, record in rows:
        numeric_value = None
        if indicator_dict.value_type == "numeric":
            parsed_value = parse_numeric_value(indicator_row.value)
            if parsed_value is not None:
                numeric_value = float(parsed_value)
                numeric_values.append(numeric_value)

        points.append(
            {
                "record_id": record.id,
                "record_display_id": record.display_id,
                "exam_date": record.exam_date.isoformat(),
                "value": indicator_row.value,
                "numeric_value": numeric_value,
                "is_abnormal": indicator_row.is_abnormal,
                "source": indicator_row.source,
            }
        )

    latest_value = points[-1]["numeric_value"] if points else None
    response = {
        "owner": {"id": owner.id, "username": owner.username},
        "indicator": indicator_dict.to_dict(),
        "series": points,
        "summary": {
            "count": len(points),
            "latest": latest_value,
            "min": min(numeric_values) if numeric_values else None,
            "max": max(numeric_values) if numeric_values else None,
            "reference_low": _to_float(indicator_dict.reference_low),
            "reference_high": _to_float(indicator_dict.reference_high),
        },
    }
    return response, 200
