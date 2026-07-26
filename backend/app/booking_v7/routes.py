from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import uuid
from zoneinfo import ZoneInfo

from flask import g, request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.booking_v7 import booking_v7_bp
from app.extensions import db
from app.models import (
    Appointment, AppointmentCapacitySlot, AppointmentEvent, BookingGroup,
    FriendRelation, Institution, NotificationOutbox, Package, PackageVersion,
    PackageVersionDomain, User, WaitlistSubscription, SelfMeasurement, IndicatorDict,
    WaitlistSubscriptionParticipant, AvailabilityNotificationEvent,
)
from app.services.domain_rules import current_package_version
from app.services.account_email import effective_account_email
from app.services.permissions import ROLE_USER, roles_required
from app.services.notifications import enqueue_user_notification


ACTIVE_STATUSES = ("unfulfilled", "awaiting_report", "fulfilled")
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")
STATUS_LABELS = {
    "unfulfilled": "预约成功",
    "awaiting_report": "等待健康数据",
    "fulfilled": "已完成",
    "invalidated": "已失效",
    "no_show": "未到检",
    "institution_cancelled": "机构取消",
    "cancelled": "已取消",
}


@booking_v7_bp.get("/booking-intake-defaults")
@roles_required(ROLE_USER)
def booking_intake_defaults():
    """Only return the signed-in user's own latest height and weight."""
    values = {}
    for code, key in (("HEIGHT", "height_cm"), ("WEIGHT", "weight_kg")):
        definition = IndicatorDict.query.filter_by(code=code).first()
        if not definition:
            continue
        measurement = SelfMeasurement.query.filter_by(
            user_id=g.current_user.id,
            indicator_dict_id=definition.id,
        ).order_by(SelfMeasurement.measured_at.desc(), SelfMeasurement.id.desc()).first()
        if measurement is not None:
            values[key] = float(measurement.value)
    return {"item": values}


def _parse_day(raw):
    try: day = date.fromisoformat(str(raw))
    except (TypeError, ValueError): return None, ({"message": "appointment_date must be YYYY-MM-DD"}, 400)
    today = datetime.now(BUSINESS_TZ).date()
    if day < today or day > today + timedelta(days=30):
        return None, ({"message": "appointments are available from today through the next 30 days"}, 400)
    return day, None


def _booked(institution_id, day):
    return Appointment.query.filter(Appointment.institution_id == institution_id,
                                    Appointment.appointment_date == day,
                                    Appointment.status.in_(ACTIVE_STATUSES)).count()


def _lock_capacity(institution, day):
    db.session.execute(update(Institution).where(Institution.id == institution.id).values(
        daily_appointment_limit=Institution.daily_appointment_limit).execution_options(synchronize_session=False))
    db.session.refresh(institution)
    slot = AppointmentCapacitySlot.query.filter_by(institution_id=institution.id, appointment_date=day).first()
    if slot is None:
        slot = AppointmentCapacitySlot(institution_id=institution.id, appointment_date=day,
                                       capacity=institution.daily_appointment_limit, revision=0)
        db.session.add(slot); db.session.flush()
    elif slot.capacity != institution.daily_appointment_limit:
        slot.capacity = institution.daily_appointment_limit; slot.revision += 1
    return slot


def _remaining(institution, day):
    return None if institution.daily_appointment_limit is None else max(institution.daily_appointment_limit - _booked(institution.id, day), 0)


def _participants(booker, raw_ids):
    if raw_ids is None: raw_ids = [booker.id]
    if not isinstance(raw_ids, list): return None, ({"message": "participant_user_ids must be an array"}, 400)
    try: ids = [int(value) for value in raw_ids]
    except (TypeError, ValueError): return None, ({"message": "participant_user_ids must contain integers"}, 400)
    if not 1 <= len(ids) <= 5: return None, ({"message": "booking group must contain between 1 and 5 participants"}, 400)
    if len(set(ids)) != len(ids): return None, ({"message": "participants must be unique"}, 400)
    users = {row.id: row for row in User.query.filter(User.id.in_(ids), User.role == "user", User.is_active.is_(True)).all()}
    if set(users) != set(ids): return None, ({"message": "all participants must be active registered users"}, 400)
    if not (booker.real_name or "").strip():
        return None, ({"message": "请先完善真实姓名再提交预约"}, 409)
    authorized_at = {}
    for subject_id in ids:
        if not (users[subject_id].real_name or "").strip():
            return None, ({"message": "所有受检者都必须先完善真实姓名"}, 409)
        if subject_id == booker.id:
            authorized_at[subject_id] = datetime.now(timezone.utc); continue
        relation = FriendRelation.query.filter_by(user_id=booker.id, friend_user_id=subject_id,
                                                   booking_auth_status=True).first()
        if not relation:
            return None, ({"message": f"booking authorization required for participant {subject_id}"}, 403)
        authorized_at[subject_id] = relation.booking_authorized_at or relation.created_at
    return [(users[user_id], authorized_at[user_id]) for user_id in ids], None


def _participant_intakes(raw_intakes, participants):
    if not isinstance(raw_intakes, list):
        return None, ({"message": "请为每位受检者填写身高和体重"}, 400)
    expected_ids = {user.id for user, _authorized_at in participants}
    parsed = {}
    for item in raw_intakes:
        if not isinstance(item, dict):
            return None, ({"message": "受检者基本资料格式不正确"}, 400)
        try:
            user_id = int(item.get("user_id"))
            height = Decimal(str(item.get("height_cm")))
            weight = Decimal(str(item.get("weight_kg")))
        except (TypeError, ValueError, InvalidOperation):
            return None, ({"message": "身高和体重必须是有效数字"}, 400)
        if user_id in parsed or user_id not in expected_ids:
            return None, ({"message": "受检者基本资料与所选成员不一致"}, 400)
        if height < Decimal("80") or height > Decimal("250"):
            return None, ({"message": "身高应在 80 至 250 厘米之间"}, 400)
        if weight < Decimal("20") or weight > Decimal("300"):
            return None, ({"message": "体重应在 20 至 300 千克之间"}, 400)
        metres = height / Decimal("100")
        bmi = (weight / (metres * metres)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        parsed[user_id] = {"height": height, "weight": weight, "bmi": bmi}
    if set(parsed) != expected_ids:
        return None, ({"message": "请完整填写每位受检者的身高和体重"}, 400)
    return parsed, None


def _appointment_conflict_payload(participants, day):
    """Return a stable, user-facing conflict without exposing internal rows."""
    conflicts = []
    for user, _authorized_at in participants:
        if Appointment.query.filter_by(user_id=user.id, active_date_key=day).first():
            conflicts.append({"user_id": user.id, "display_name": user.real_name or user.username})
    if not conflicts:
        return None
    return {
        "code": "APPOINTMENT_DATE_CONFLICT",
        "message": "当天已有预约，请先查看或取消原预约后再选择其他日期",
        "appointment_date": day.isoformat(),
        "conflicts": conflicts,
    }


def _masked_health_id(value):
    text = str(value or "").strip()
    if len(text) <= 4:
        return "****" if text else "未设置"
    return f"{text[:2]}{'*' * max(4, len(text) - 4)}{text[-2:]}"


def _package(institution_id, package_id):
    package = Package.query.filter_by(id=package_id, institution_id=institution_id, is_active=True).first()
    if not package: return None, None, ({"message": "active approved package not found"}, 404)
    version = current_package_version(package)
    if not version: return None, None, ({"message": "package has no approved version"}, 409)
    domains = [row.domain.to_dict() for row in version.domains if row.domain]
    if (version.package_type == "special" and len(domains) != 1) or (version.package_type == "combined" and len(domains) < 2):
        return None, None, ({"message": "package version has an invalid domain definition"}, 409)
    return package, version, domains


def _group_payload(row):
    payload = row.to_dict()
    institution = db.session.get(Institution, row.institution_id)
    package = db.session.get(Package, row.package_id)
    statuses = []
    for appointment in row.appointments:
        if appointment.status not in statuses:
            statuses.append(appointment.status)
    payload.update({
        "institution": ({"id": institution.id, "name": institution.name,
                         "branch_name": institution.branch_name} if institution else None),
        "package": ({"id": package.id, "name": row.package_name_snapshot or package.name,
                     "domains": row.domain_snapshot or []} if package else None),
        "participant_names": [appointment.user_name_snapshot for appointment in row.appointments],
        "status_codes": statuses,
        "status_labels": [STATUS_LABELS.get(status, status) for status in statuses],
        "can_cancel": bool(row.appointments) and all(item.status == "unfulfilled" for item in row.appointments),
    })
    return payload


def _waitlist_payload(row):
    payload = row.to_dict()
    institution = db.session.get(Institution, row.institution_id)
    package = db.session.get(Package, row.package_id)
    latest_event = AvailabilityNotificationEvent.query.filter_by(subscription_id=row.id).order_by(
        AvailabilityNotificationEvent.created_at.desc(), AvailabilityNotificationEvent.id.desc()
    ).first()
    payload.update({
        "institution": ({"id": institution.id, "name": institution.name,
                         "branch_name": institution.branch_name} if institution else None),
        "package": ({"id": package.id, "name": package.name} if package else None),
        "status_label": {"active": "等待可预约提醒", "closed": "已完成预约",
                         "cancelled": "已取消", "invalid": "已失效"}.get(row.status, row.status),
        "last_notification": ({"sent_at": latest_event.created_at.isoformat(),
                               "remaining": latest_event.remaining_snapshot} if latest_event else None),
        "notice": "空位提醒不会保留名额，收到提醒后仍需重新提交预约。",
    })
    return payload


def _reset_unsatisfied(institution_id, day, remaining):
    if remaining is None: return
    for row in WaitlistSubscription.query.filter_by(institution_id=institution_id, appointment_date=day, status="active").all():
        if remaining < row.party_size: row.last_satisfied_revision = None


def enqueue_available(institution, day, slot):
    remaining = _remaining(institution, day)
    if remaining is None: remaining = 999999
    for sub in WaitlistSubscription.query.filter_by(institution_id=institution.id, appointment_date=day, status="active").all():
        if remaining < sub.party_size:
            sub.last_satisfied_revision = None
            continue
        if sub.last_satisfied_revision is not None:
            continue
        valid = True
        for participant in sub.participants:
            user = db.session.get(User, participant.subject_user_id)
            if not user or not user.is_active:
                valid = False; break
            if Appointment.query.filter_by(user_id=user.id, active_date_key=day).first():
                valid = False; break
            if user.id != sub.subscriber_user_id and not FriendRelation.query.filter_by(
                user_id=sub.subscriber_user_id, friend_user_id=user.id, booking_auth_status=True).first():
                valid = False; break
        if not valid:
            sub.status = "invalid"; sub.closed_at = datetime.now(timezone.utc); continue
        event = AvailabilityNotificationEvent(subscription_id=sub.id, capacity_revision=slot.revision,
                                              remaining_snapshot=remaining)
        db.session.add(event)
        key = f"waitlist:{sub.id}:revision:{slot.revision}"
        subscriber = db.session.get(User, sub.subscriber_user_id)
        email_payload = {
            "subscription_id": sub.id,
            "institution": institution.name,
            "branch": institution.branch_name,
            "appointment_date": day.isoformat(),
            "party_size": sub.party_size,
            "message": "名额先到先得，本邮件不代表预约成功或已经保留名额。",
            "login_url": "/appointments",
        }
        if subscriber is not None:
            enqueue_user_notification(
                subscriber,
                event_type="waitlist_available",
                idempotency_key=key,
                title="您关注的体检日期出现空位",
                body=f"{institution.name}·{institution.branch_name}在{day.isoformat()}出现可预约名额，请尽快重新确认预约；本提醒不代表已保留名额。",
                action_url="/appointments",
                payload={"subscription_id": sub.id},
                email_payload=email_payload,
            )
        else:
            db.session.add(NotificationOutbox(
                event_type="waitlist_available",
                idempotency_key=key,
                recipient=sub.notification_email,
                payload=email_payload,
            ))
        sub.last_satisfied_revision = slot.revision


@booking_v7_bp.get("/booking-groups")
@roles_required(ROLE_USER)
def groups():
    query = BookingGroup.query.filter_by(booked_by_user_id=g.current_user.id)
    try:
        start = date.fromisoformat(request.args["start_date"]) if request.args.get("start_date") else None
        end = date.fromisoformat(request.args["end_date"]) if request.args.get("end_date") else None
    except ValueError:
        return {"message": "日期范围格式应为 YYYY-MM-DD"}, 400
    if start and end and start > end:
        return {"message": "开始日期不能晚于结束日期"}, 400
    if start: query = query.filter(BookingGroup.appointment_date >= start)
    if end: query = query.filter(BookingGroup.appointment_date <= end)
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 10, type=int) or 10, 1), 50)
    total = query.count()
    rows = query.order_by(
        BookingGroup.appointment_date.desc(),
        BookingGroup.id.desc(),
    ).offset((page - 1) * size).limit(size).all()
    return {
        "items": [_group_payload(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200


@booking_v7_bp.post("/booking-groups")
@roles_required(ROLE_USER)
def create_group():
    payload = request.get_json(silent=True) or {}
    day, error = _parse_day(payload.get("appointment_date"))
    if error: return error
    try: institution_id, package_id = int(payload.get("institution_id")), int(payload.get("package_id"))
    except (TypeError, ValueError): return {"message": "institution_id and package_id must be integers"}, 400
    institution = Institution.query.filter_by(id=institution_id, is_active=True).first()
    if not institution: return {"message": "institution not found"}, 404
    package, version, result = _package(institution.id, package_id)
    if package is None: return result
    domain_snapshot = result
    participants, error = _participants(g.current_user, payload.get("participant_user_ids"))
    if error: return error
    intakes, error = _participant_intakes(payload.get("participant_intakes"), participants)
    if error: return error
    conflict = _appointment_conflict_payload(participants, day)
    if conflict:
        return conflict, 409
    confirmed = payload.get("notice_confirmed") is True
    if version.booking_notice_snapshot and not confirmed:
        return {"message": "package booking notice must be confirmed"}, 400
    slot = _lock_capacity(institution, day)
    remaining = _remaining(institution, day)
    if remaining is not None and remaining < len(participants):
        db.session.rollback()
        return {"message": "剩余名额不足以容纳整个预约组", "code": "APPOINTMENT_FULL",
                "remaining": remaining, "party_size": len(participants)}, 409
    group = BookingGroup(group_code=f"BG-{uuid.uuid4().hex[:12].upper()}", booked_by_user_id=g.current_user.id,
        institution_id=institution.id, package_id=package.id, package_version_id=version.id,
        appointment_date=day, party_size=len(participants), package_name_snapshot=version.name_snapshot,
        package_price_snapshot=version.price_snapshot, domain_snapshot=domain_snapshot,
        booking_notice_snapshot=version.booking_notice_snapshot, notice_version_snapshot=version.version_number,
        notice_confirmed_at=datetime.now(timezone.utc), contact_snapshot={"email": effective_account_email(g.current_user), "phone": g.current_user.phone})
    db.session.add(group); db.session.flush()
    now = datetime.now(timezone.utc)
    for user, _authorized_at in participants:
        intake = intakes[user.id]
        appointment = Appointment(user_id=user.id, booked_by_user_id=g.current_user.id,
            booking_group_id=group.id, institution_id=institution.id, package_id=package.id,
            package_version_id=version.id, appointment_date=day, active_date_key=day, status="unfulfilled",
            user_name_snapshot=user.real_name or user.username, user_health_id_snapshot=user.health_id,
            user_birth_date_snapshot=user.birth_date, user_gender_snapshot=user.gender,
            user_contact_snapshot=user.phone or effective_account_email(user), package_name_snapshot=version.name_snapshot,
            package_price_snapshot=version.price_snapshot,
            height_cm_snapshot=intake["height"], weight_kg_snapshot=intake["weight"],
            bmi_snapshot=intake["bmi"], allergy_history_snapshot=user.allergy_history,
            medical_history_snapshot=user.medical_history, intake_captured_at=now)
        db.session.add(appointment); db.session.flush()
        db.session.add(AppointmentEvent(appointment_id=appointment.id, event_type="booked",
                                        status_snapshot="unfulfilled", message="预约成功",
                                        actor_user_id=g.current_user.id, occurred_at=now))
    slot.revision += 1
    after = _remaining(institution, day)
    _reset_unsatisfied(institution.id, day, after)
    recipient = institution.notification_email or next(
        (effective_account_email(admin) for admin in institution.administrators if effective_account_email(admin)),
        None,
    )
    if institution.notification_enabled and recipient:
        db.session.add(NotificationOutbox(event_type="booking_group_created",
            idempotency_key=f"booking-group:{group.id}:created", recipient=recipient,
            payload={"group_code": group.group_code, "institution": institution.name,
                     "appointment_date": day.isoformat(), "package": version.name_snapshot,
                     "party_size": len(participants), "login_url": "/login"}))
        if remaining is not None and remaining > 0 and after == 0:
            db.session.add(NotificationOutbox(event_type="appointment_date_full",
                idempotency_key=f"institution:{institution.id}:date:{day.isoformat()}:full:{slot.revision}",
                recipient=recipient, payload={"institution": institution.name,
                    "appointment_date": day.isoformat(), "message": "该日期预约名额已满", "login_url": "/login"}))
    # User confirmations are independent of the institution's notification switch.
    # One logical account receives one message; accounts sharing an address still
    # receive their own traceable confirmation.
    participant_by_id = {user.id: user for user, _authorized_at in participants}
    notification_users = dict(participant_by_id)
    notification_users[g.current_user.id] = g.current_user
    participant_summary = [
        {"name": user.real_name or user.username, "health_id_masked": _masked_health_id(user.health_id)}
        for user, _authorized_at in participants
    ]
    participant_text = "、".join(
        f"{item['name']}（{item['health_id_masked']}）" for item in participant_summary
    )
    for notification_user in notification_users.values():
        is_organizer = notification_user.id == g.current_user.id
        own = participant_by_id.get(notification_user.id)
        email_payload = {
                "group_code": group.group_code,
                "institution": institution.name,
                "branch": institution.branch_name,
                "address": institution.address,
                "consult_phone": institution.consult_phone,
                "appointment_date": day.isoformat(),
                "package": version.name_snapshot,
                "booking_notice": version.booking_notice_snapshot,
                "recipient_name": notification_user.real_name or "用户",
                "is_organizer": is_organizer,
                "participant": ({
                    "name": own.real_name,
                    "health_id_masked": _masked_health_id(own.health_id),
                } if own else None),
                "participants": participant_summary if is_organizer else [],
                "preparation_items": [
                    "身份证原件",
                    "HealthDoc 预约凭证",
                    "病历本",
                    "既往体检报告或影像资料",
                    "正在使用的药物清单",
                ],
                "login_url": "/appointments",
            }
        enqueue_user_notification(
            notification_user,
            event_type="booking_user_confirmed",
            idempotency_key=f"booking-group:{group.id}:user:{notification_user.id}:confirmed",
            title="体检预约成功",
            body=(
                f"{day.isoformat()} {institution.name}·{institution.branch_name}，地址：{institution.address}，"
                f"电话：{institution.consult_phone or '请在机构详情查看'}，套餐：{version.name_snapshot}，"
                f"受检者：{participant_text}。请携带身份证原件、HealthDoc预约凭证、病历本、"
                f"既往体检报告或影像资料和用药清单。机构已审核须知："
                f"{version.booking_notice_snapshot or '暂无额外准备要求'}"
            ),
            action_url="/appointments",
            payload={"booking_group_id": group.id},
            email_payload=email_payload,
        )
    participant_ids = {user.id for user, _ in participants}
    for sub in WaitlistSubscription.query.filter_by(subscriber_user_id=g.current_user.id,
            institution_id=institution.id, package_id=package.id, appointment_date=day,
            party_size=len(participants), status="active").all():
        if {row.subject_user_id for row in sub.participants} == participant_ids:
            sub.status = "closed"; sub.closed_at = now
    try: db.session.commit()
    except IntegrityError:
        db.session.rollback()
        conflict = _appointment_conflict_payload(participants, day)
        return conflict or {
            "code": "APPOINTMENT_DATE_CONFLICT",
            "message": "当天已有预约，请先查看或取消原预约后再选择其他日期",
            "appointment_date": day.isoformat(),
            "conflicts": [],
        }, 409
    return {"item": _group_payload(group)}, 201


@booking_v7_bp.post("/booking-groups/<int:group_id>/cancel")
@roles_required(ROLE_USER)
def cancel_group(group_id):
    group = BookingGroup.query.filter_by(id=group_id, booked_by_user_id=g.current_user.id).first()
    if not group: return {"message": "booking group not found"}, 404
    if any(row.status != "unfulfilled" for row in group.appointments):
        return {"message": "only a wholly unfulfilled booking group can be cancelled"}, 409
    institution = db.session.get(Institution, group.institution_id)
    now = datetime.now(timezone.utc)
    for row in group.appointments:
        row.status = "cancelled"; row.active_date_key = None; row.cancelled_at = now
        db.session.add(AppointmentEvent(appointment_id=row.id, event_type="cancelled",
            status_snapshot="cancelled", message="预约组已取消", actor_user_id=g.current_user.id, occurred_at=now))
    slot = _lock_capacity(institution, group.appointment_date); slot.revision += 1
    enqueue_available(institution, group.appointment_date, slot)
    db.session.commit(); return {"item": _group_payload(group)}, 200


@booking_v7_bp.get("/waitlist-subscriptions")
@roles_required(ROLE_USER)
def waitlists():
    query = WaitlistSubscription.query.filter_by(subscriber_user_id=g.current_user.id)
    active_count = query.filter_by(status="active").count()
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 15, type=int) or 15, 1), 100)
    total = query.count()
    rows = query.order_by(WaitlistSubscription.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {
        "items": [_waitlist_payload(row) for row in rows],
        "active_count": active_count,
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200


@booking_v7_bp.post("/waitlist-subscriptions")
@roles_required(ROLE_USER)
def create_waitlist():
    payload = request.get_json(silent=True) or {}
    day, error = _parse_day(payload.get("appointment_date"))
    if error: return error
    try: institution_id, package_id = int(payload.get("institution_id")), int(payload.get("package_id"))
    except (TypeError, ValueError): return {"message": "institution_id and package_id must be integers"}, 400
    institution = Institution.query.filter_by(id=institution_id, is_active=True).first()
    if not institution: return {"message": "institution not found"}, 404
    package, version, result = _package(institution.id, package_id)
    if package is None: return result
    participants, error = _participants(g.current_user, payload.get("participant_user_ids"))
    if error: return error
    if not effective_account_email(g.current_user):
        return {"message": "订阅空位提醒前，请先绑定通知邮箱"}, 400
    _lock_capacity(institution, day)
    remaining = _remaining(institution, day)
    if remaining is None or remaining >= len(participants):
        db.session.rollback()
        return {"message": "当前名额充足，请直接提交正式预约", "code": "BOOK_NOW"}, 409
    existing = WaitlistSubscription.query.filter_by(subscriber_user_id=g.current_user.id,
        institution_id=institution.id, package_id=package.id, appointment_date=day,
        party_size=len(participants), status="active").first()
    if existing: db.session.rollback(); return {"message": "equivalent active subscription already exists"}, 409
    sub = WaitlistSubscription(subscriber_user_id=g.current_user.id, institution_id=institution.id,
        package_id=package.id, package_version_id=version.id, appointment_date=day,
        party_size=len(participants), notification_email=effective_account_email(g.current_user))
    db.session.add(sub); db.session.flush()
    for user, authorized_at in participants:
        db.session.add(WaitlistSubscriptionParticipant(subscription_id=sub.id, subject_user_id=user.id,
            name_snapshot=user.real_name or user.username, health_id_snapshot=user.health_id,
            booking_authorized_at=authorized_at))
    db.session.commit()
    return {"item": _waitlist_payload(sub)}, 201


@booking_v7_bp.delete("/waitlist-subscriptions/<int:subscription_id>")
@roles_required(ROLE_USER)
def cancel_waitlist(subscription_id):
    row = WaitlistSubscription.query.filter_by(id=subscription_id, subscriber_user_id=g.current_user.id).first()
    if not row: return {"message": "waitlist subscription not found"}, 404
    if row.status != "active": return {"message": "waitlist subscription is not active"}, 409
    row.status = "cancelled"; row.closed_at = datetime.now(timezone.utc); db.session.commit()
    return {"item": _waitlist_payload(row)}, 200
