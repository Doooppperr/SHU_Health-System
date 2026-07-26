from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from flask import g, request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.appointments import appointments_bp
from app.extensions import db
from app.models import Appointment, AppointmentEvent, Institution, Organization, Package
from app.services.permissions import ROLE_USER, roles_required
from app.services.notifications import enqueue_user_notification


ACTIVE_CAPACITY_STATUSES = ("unfulfilled", "awaiting_report", "fulfilled")
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _business_today():
    return datetime.now(BUSINESS_TIMEZONE).date()


def _parse_bookable_date(raw_value):
    try:
        appointment_date = date.fromisoformat(str(raw_value))
    except (TypeError, ValueError):
        return None, ({"message": "appointment_date must be YYYY-MM-DD"}, 400)
    today = _business_today()
    if appointment_date < today or appointment_date > today + timedelta(days=30):
        return None, ({"message": "appointments are available from today through the next 30 days"}, 400)
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
        "institution": institution.to_dict(),
        "appointment_date": appointment_date.isoformat(),
        "daily_limit": limit,
        "booked_count": booked,
        "remaining": remaining,
        "is_full": remaining == 0 if remaining is not None else False,
        "packages": [
            item.to_dict()
            for item in Package.query.filter_by(institution_id=institution.id, is_active=True)
            .order_by(Package.id.asc()).all()
        ],
    }


@appointments_bp.get("/availability")
@roles_required(ROLE_USER)
def availability():
    appointment_date, error = _parse_bookable_date(request.args.get("appointment_date"))
    if error:
        return error
    query = Institution.query.filter_by(is_active=True)
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
def create_appointment():
    payload = request.get_json(silent=True) or {}
    appointment_date, error = _parse_bookable_date(payload.get("appointment_date"))
    if error:
        return error
    try:
        institution_id = int(payload.get("institution_id"))
        package_id = int(payload.get("package_id"))
    except (TypeError, ValueError):
        return {"message": "institution_id and package_id must be integers"}, 400
    if not (g.current_user.real_name or "").strip():
        return {"message": "请先完善真实姓名再提交预约"}, 409
    try:
        height = Decimal(str(payload.get("height_cm")))
        weight = Decimal(str(payload.get("weight_kg")))
    except (TypeError, ValueError, InvalidOperation):
        return {"message": "请填写有效的身高和体重"}, 400
    if not Decimal("80") <= height <= Decimal("250"):
        return {"message": "身高应在 80 至 250 厘米之间"}, 400
    if not Decimal("20") <= weight <= Decimal("300"):
        return {"message": "体重应在 20 至 300 千克之间"}, 400
    metres = height / Decimal("100")
    bmi = (weight / (metres * metres)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    institution = Institution.query.filter_by(id=institution_id, is_active=True).first()
    package = Package.query.filter_by(id=package_id, institution_id=institution_id, is_active=True).first()
    if institution is None:
        return {"message": "institution not found"}, 404
    if package is None:
        return {"message": "active approved package not found"}, 404

    # A no-op row update serializes capacity checks in both SQLite and openGauss.
    db.session.execute(
        update(Institution).where(Institution.id == institution.id).values(
            daily_appointment_limit=Institution.daily_appointment_limit
        ).execution_options(synchronize_session=False)
    )
    db.session.refresh(institution)
    booked = _booked_count(institution.id, appointment_date)
    if institution.daily_appointment_limit is not None and booked >= institution.daily_appointment_limit:
        db.session.rollback()
        return {"message": "今日已无预约名额", "code": "APPOINTMENT_FULL"}, 409

    now = datetime.now(timezone.utc)
    version = next((row for row in package.versions if row.id == package.current_version_id), None)
    item = Appointment(
        user_id=g.current_user.id,
        booked_by_user_id=g.current_user.id,
        institution_id=institution.id,
        package_id=package.id,
        package_version_id=version.id if version else None,
        appointment_date=appointment_date,
        active_date_key=appointment_date,
        status="unfulfilled",
        user_name_snapshot=g.current_user.real_name,
        user_health_id_snapshot=g.current_user.health_id,
        user_birth_date_snapshot=g.current_user.birth_date,
        user_gender_snapshot=g.current_user.gender,
        user_contact_snapshot=g.current_user.phone or g.current_user.email,
        height_cm_snapshot=height,
        weight_kg_snapshot=weight,
        bmi_snapshot=bmi,
        allergy_history_snapshot=g.current_user.allergy_history,
        medical_history_snapshot=g.current_user.medical_history,
        intake_captured_at=now,
        package_name_snapshot=package.name,
        package_price_snapshot=package.price,
    )
    db.session.add(item)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return {"message": "同一用户同一天只能保留一条有效预约"}, 409
    db.session.add(AppointmentEvent(
        appointment_id=item.id,
        event_type="booked",
        status_snapshot="unfulfilled",
        message="预约成功",
        actor_user_id=g.current_user.id,
        occurred_at=now,
    ))
    notice = version.booking_notice_snapshot if version else package.booking_notice
    enqueue_user_notification(
        g.current_user,
        event_type="booking_user_confirmed",
        idempotency_key=f"appointment:{item.id}:user:{g.current_user.id}:confirmed",
        title="体检预约成功",
        body=(
            f"{appointment_date.isoformat()} {institution.name}·{institution.branch_name}，"
            f"地址：{institution.address}，电话：{institution.consult_phone or '请在机构详情查看'}，"
            f"套餐：{package.name}。请携带身份证原件、HealthDoc预约凭证、病历本、"
            f"既往体检报告或影像资料和用药清单。机构已审核须知：{notice or '暂无额外准备要求'}"
        ),
        action_url="/appointments",
        payload={"appointment_id": item.id},
        email_payload={
            "institution": institution.name,
            "branch": institution.branch_name,
            "address": institution.address,
            "consult_phone": institution.consult_phone,
            "appointment_date": appointment_date.isoformat(),
            "package": package.name,
            "booking_notice": notice,
            "recipient_name": g.current_user.real_name,
            "participant": {
                "name": g.current_user.real_name,
                "health_id_masked": f"{g.current_user.health_id[:3]}****{g.current_user.health_id[-3:]}",
            },
            "preparation_items": ["身份证原件", "HealthDoc 预约凭证", "病历本", "既往体检报告或影像资料", "正在使用的药物清单"],
            "login_url": "/appointments",
        },
    )
    db.session.commit()
    return {"item": item.to_dict()}, 201


@appointments_bp.post("/<int:appointment_id>/cancel")
@roles_required(ROLE_USER)
def cancel_appointment(appointment_id):
    item = Appointment.query.filter_by(id=appointment_id, user_id=g.current_user.id).first()
    if item is None:
        return {"message": "appointment not found"}, 404
    if item.status != "unfulfilled":
        return {"message": "only unfulfilled appointments can be cancelled"}, 409
    item.status = "cancelled"
    item.active_date_key = None
    item.cancelled_at = datetime.now(timezone.utc)
    db.session.add(AppointmentEvent(appointment_id=item.id, event_type="cancelled", status_snapshot="cancelled",
                                    message="预约已取消", actor_user_id=g.current_user.id, occurred_at=item.cancelled_at))
    from app.booking_v7.routes import _lock_capacity, enqueue_available
    slot = _lock_capacity(item.institution, item.appointment_date); slot.revision += 1
    enqueue_available(item.institution, item.appointment_date, slot)
    db.session.commit()
    return {"item": item.to_dict()}, 200
