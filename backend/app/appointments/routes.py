from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import g, request
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.appointments import appointments_bp
from app.extensions import db
from app.models import Appointment, AppointmentEvent, Institution, Organization, Package, PaymentOrderItem
from app.public_api.routes import public_package_payload
from app.services.permissions import ROLE_USER, roles_required
from app.services.user_access import complete_profile_required


ACTIVE_CAPACITY_STATUSES = ("pending_payment", "unfulfilled", "awaiting_report", "fulfilled")
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _cancel_appointment_cas(appointment_id, cancelled_at):
    """Atomically release one appointment only while it is still pending.

    Loading an appointment and assigning ``item.status`` is not sufficient:
    an institution may confirm attendance on another connection between those
    two operations.  Keep this small helper separate so every caller can make
    the state transition before emitting events or releasing capacity.
    """
    try:
        result = db.session.execute(
            update(Appointment)
            .where(
                Appointment.id == appointment_id,
                Appointment.status == "unfulfilled",
            )
            .values(
                status="cancelled",
                active_date_key=None,
                cancelled_at=cancelled_at,
            )
            .execution_options(synchronize_session=False)
        )
    except OperationalError:
        db.session.rollback()
        return False
    return result.rowcount == 1


def _business_today():
    return datetime.now(BUSINESS_TIMEZONE).date()


def _parse_bookable_date(raw_value):
    try:
        appointment_date = date.fromisoformat(str(raw_value))
    except (TypeError, ValueError):
        return None, ({"message": "appointment_date must be YYYY-MM-DD"}, 400)
    today = _business_today()
    if (
        appointment_date < today + timedelta(days=1)
        or appointment_date > today + timedelta(days=30)
    ):
        return None, ({
            "message": "预约日期须为明天起30天内",
            "code": "BOOKING_DATE_INVALID",
        }, 400)
    return appointment_date, None


def _booked_count(institution_id, appointment_date):
    return Appointment.query.filter(
        Appointment.institution_id == institution_id,
        Appointment.appointment_date == appointment_date,
        Appointment.status.in_(ACTIVE_CAPACITY_STATUSES),
    ).count()


def _availability_payload(institution, appointment_date):
    booked = _booked_count(institution.id, appointment_date)
    limit = institution.daily_appointment_limit
    remaining = None if limit is None else max(limit - booked, 0)
    return {
        "institution": {
            "id": institution.id,
            "organization_id": institution.organization_id,
            "name": (
                institution.organization.name
                if institution.organization
                else institution.name
            ),
            "branch_name": institution.branch_name,
            "address": institution.address,
            "district": institution.district,
            "metro_info": institution.metro_info,
            "consult_phone": institution.consult_phone,
            "description": institution.description,
            "cover_image_url": (
                institution.images[0].image_url
                if institution.images
                else institution.logo_url
            ),
        },
        "appointment_date": appointment_date.isoformat(),
        "daily_limit": limit,
        "booked_count": booked,
        "remaining": remaining,
        "is_full": remaining == 0 if remaining is not None else False,
        "packages": [
            public_package_payload(item)
            for item in Package.query.filter(
                Package.institution_id == institution.id,
                Package.is_active.is_(True),
                Package.current_version_id.is_not(None),
            )
            .order_by(Package.id.asc()).all()
        ],
    }


@appointments_bp.get("/availability")
@roles_required(ROLE_USER)
def availability():
    appointment_date, error = _parse_bookable_date(request.args.get("appointment_date"))
    if error:
        return error
    query = Institution.query.join(Institution.organization).filter(
        Institution.is_active.is_(True),
        Institution.operations_suspended_at.is_(None),
        Organization.is_active.is_(True),
    )
    keyword = (request.args.get("q") or "").strip()
    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter(db.or_(
            Institution.name.ilike(pattern),
            Institution.branch_name.ilike(pattern),
            Institution.district.ilike(pattern),
            Institution.address.ilike(pattern),
            Institution.metro_info.ilike(pattern),
            Institution.organization.has(Organization.name.ilike(pattern)),
        ))
    institutions = query.order_by(Institution.id.asc()).limit(50).all()
    return {"appointment_date": appointment_date.isoformat(), "items": [_availability_payload(item, appointment_date) for item in institutions]}, 200


@appointments_bp.get("")
@roles_required(ROLE_USER)
def list_appointments():
    from app.services.finance import run_due_finance_tasks
    run_due_finance_tasks()
    db.session.commit()
    query = Appointment.query.filter_by(user_id=g.current_user.id)
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 15, type=int) or 15, 1), 100)
    total = query.count()
    rows = query.order_by(Appointment.appointment_date.desc(), Appointment.id.desc()).offset(
        (page - 1) * size
    ).limit(size).all()
    return {
        "items": [item.to_dict() for item in rows],
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200


@appointments_bp.post("")
@roles_required(ROLE_USER)
@complete_profile_required
def create_appointment():
    """Compatibility adapter for the canonical v13 group-booking workflow.

    The legacy endpoint remains available to older clients, but it no longer
    has an independent write path. A one-person request is normalized to a
    ``self`` participant so notice confirmation, capacity locking, participant
    authorization snapshots, receipts, events, and notifications are exactly
    the same as a booking-group request.
    """
    payload = request.get_json(silent=True) or {}
    normalized = {
        "institution_id": payload.get("institution_id"),
        "package_id": payload.get("package_id"),
        "appointment_date": payload.get("appointment_date"),
        "notice_confirmed": payload.get("notice_confirmed") is True,
        "participants": [
            {
                "type": "self",
                "height_cm": payload.get("height_cm"),
                "weight_kg": payload.get("weight_kg"),
            }
        ],
    }
    from app.booking_v7.routes import create_booking_group_for_user

    result, status = create_booking_group_for_user(
        g.current_user,
        normalized,
    )
    if status != 201:
        return result, status
    group_payload = result["item"]
    item = Appointment.query.filter_by(
        booking_group_id=group_payload["id"],
        user_id=g.current_user.id,
    ).one()
    return {
        "item": item.to_dict(),
        "booking_group": group_payload,
        "payment_order": result.get("payment_order"),
    }, 201


@appointments_bp.post("/<int:appointment_id>/cancel")
@roles_required(ROLE_USER)
@complete_profile_required
def cancel_appointment(appointment_id):
    item = Appointment.query.filter(
        Appointment.id == appointment_id,
        db.or_(
            Appointment.user_id == g.current_user.id,
            Appointment.booked_by_user_id == g.current_user.id,
        ),
    ).first()
    if item is None:
        return {"message": "appointment not found"}, 404
    if item.status != "unfulfilled":
        return {"message": "only unfulfilled appointments can be cancelled"}, 409
    cancelled_at = datetime.now(timezone.utc)
    if not _cancel_appointment_cas(item.id, cancelled_at):
        db.session.rollback()
        return {
            "message": "appointment state changed; reload and retry",
            "code": "APPOINTMENT_STATE_CONFLICT",
        }, 409
    event_message = (
        "代预约人已取消该受检者预约"
        if item.user_id != g.current_user.id
        else "预约已取消"
    )
    db.session.add(AppointmentEvent(
        appointment_id=item.id,
        event_type="cancelled",
        status_snapshot="cancelled",
        message=event_message,
        actor_user_id=g.current_user.id,
        occurred_at=cancelled_at,
    ))
    payment_item = PaymentOrderItem.query.filter_by(appointment_id=item.id).first()
    if payment_item is not None:
        from app.services.finance import refund_item
        refund_item(payment_item, actor_user=g.current_user, reason="user_cancellation")
    from app.booking_v7.routes import _lock_capacity, enqueue_available
    slot = _lock_capacity(item.institution, item.appointment_date); slot.revision += 1
    enqueue_available(item.institution, item.appointment_date, slot)
    db.session.commit()
    return {"item": item.to_dict()}, 200
