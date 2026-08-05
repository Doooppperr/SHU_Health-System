from datetime import date, datetime, timedelta, timezone
import uuid
from zoneinfo import ZoneInfo

from flask import g, request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.booking_v7 import booking_v7_bp
from app.extensions import db
from app.models import (
    Appointment, AppointmentCapacitySlot, AppointmentEvent, BookingGroup,
    BookingParticipantAuthorization, FriendRelation, Institution,
    NotificationOutbox, Organization, Package, PackageVersion,
    PackageVersionDomain, User, WaitlistSubscription,
    WaitlistSubscriptionParticipant, AvailabilityNotificationEvent, PaymentOrder,
)
from app.services.domain_rules import current_package_version
from app.services.account_email import effective_account_email
from app.services.permissions import ROLE_USER, roles_required
from app.services.notifications import enqueue_user_notification
from app.services.booking_participants import (
    consume_participant_tokens,
    issue_participant_token,
    latest_intake_defaults,
    masked_name,
    participant_intakes,
    resolve_booking_participants,
)
from app.services.user_access import (
    complete_profile_required,
    profile_completion_error,
)


ACTIVE_STATUSES = ("pending_payment", "unfulfilled", "awaiting_report", "fulfilled")
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")
STATUS_LABELS = {
    "pending_payment": "待付款",
    "payment_expired": "付款超时",
    "unfulfilled": "预约成功",
    "awaiting_report": "等待健康数据",
    "fulfilled": "已完成",
    "invalidated": "已失效",
    "no_show": "未到检",
    "institution_cancelled": "机构取消",
    "cancelled": "已取消",
}
RECEIPT_EVENT_TYPES = {
    "order_created",
    "payment_completed",
    "payment_expired",
    "booked",
    "attended",
    "report_uploaded",
    "pending_review",
    "submitted_review",
    "report_published",
    "archived",
    "cancelled",
    "invalidated",
    "no_show",
    "institution_cancelled",
}


@booking_v7_bp.get("/booking-intake-defaults")
@roles_required(ROLE_USER)
def booking_intake_defaults():
    """Only return the signed-in user's own latest height and weight."""
    values = {
        key: float(value)
        for key, value in latest_intake_defaults(g.current_user.id).items()
        if value is not None
    }
    return {"item": values}


@booking_v7_bp.post("/booking-participants/resolve")
@roles_required(ROLE_USER)
@complete_profile_required
def resolve_booking_participant():
    payload = request.get_json(silent=True) or {}
    item, error = issue_participant_token(
        g.current_user,
        payload.get("health_id"),
    )
    if error:
        return error
    db.session.commit()
    return {"item": item}, 200


def _parse_day(raw):
    try: day = date.fromisoformat(str(raw))
    except (TypeError, ValueError): return None, ({"message": "appointment_date must be YYYY-MM-DD"}, 400)
    today = datetime.now(BUSINESS_TZ).date()
    if day < today + timedelta(days=1) or day > today + timedelta(days=30):
        return None, ({
            "message": "预约日期须为明天起30天内",
            "code": "BOOKING_DATE_INVALID",
        }, 400)
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


def _appointment_conflict_payload(participants, day):
    """Return a stable, user-facing conflict without exposing internal rows."""
    conflicts = []
    for participant in participants:
        user = participant["user"]
        if Appointment.query.filter_by(user_id=user.id, active_date_key=day).first():
            conflicts.append({
                "user_id": (
                    user.id
                    if participant["participant_type"]
                    not in {"health_code", "health_code_token"}
                    else None
                ),
                "display_name": (
                    masked_name(user)
                    if participant["participant_type"]
                    in {"health_code", "health_code_token"}
                    else user.real_name or user.username
                ),
            })
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


def _receipt_progress(appointment):
    """Return operational progress without exposing a participant's report.

    A booking organizer is allowed to know whether the appointment they made
    has reached upload, review, or publication, but is not allowed to receive
    report identifiers, findings, doctor names, or event messages.  Keep this
    projection deliberately narrower than ``Appointment.to_dict``.
    """
    report = appointment.report
    receipt_events = [
        event for event in appointment.events
        if event.event_type in RECEIPT_EVENT_TYPES
    ]
    event_types = {event.event_type for event in receipt_events}
    stage = "booked"
    if (
        appointment.status == "awaiting_report"
        or appointment.attended_at is not None
        or "attended" in event_types
    ):
        stage = "attended"
    if (
        (report is not None and report.status == "draft")
        or "report_uploaded" in event_types
    ):
        stage = "report_uploaded"
    if (
        (report is not None and report.status == "pending_review")
        or "pending_review" in event_types
        or "submitted_review" in event_types
    ):
        stage = "pending_review"
    if (
        (report is not None and report.status == "published")
        or appointment.status == "fulfilled"
        or appointment.fulfilled_at is not None
        or "report_published" in event_types
        or "archived" in event_types
    ):
        stage = "published"
    if appointment.status in {
        "cancelled",
        "invalidated",
        "no_show",
        "institution_cancelled",
    }:
        stage = appointment.status

    return {
        "progress_stage": stage,
        "report_status": report.status if report is not None else None,
        "created_at": (
            appointment.created_at.isoformat()
            if appointment.created_at else None
        ),
        "attended_at": (
            appointment.attended_at.isoformat()
            if appointment.attended_at else None
        ),
        "cancelled_at": (
            appointment.cancelled_at.isoformat()
            if appointment.cancelled_at else None
        ),
        "invalidated_at": (
            appointment.invalidated_at.isoformat()
            if appointment.invalidated_at else None
        ),
        "fulfilled_at": (
            appointment.fulfilled_at.isoformat()
            if appointment.fulfilled_at else None
        ),
        "submitted_for_review_at": (
            report.submitted_for_review_at.isoformat()
            if report is not None and report.submitted_for_review_at else None
        ),
        "published_at": (
            report.published_at.isoformat()
            if report is not None and report.published_at else None
        ),
        "events": [
            {
                "type": event.event_type,
                "status": event.status_snapshot,
                "occurred_at": (
                    event.occurred_at.isoformat()
                    if event.occurred_at else None
                ),
            }
            for event in receipt_events
        ],
    }


def _group_payload(row):
    payload = row.to_dict(include_appointments=False)
    institution = db.session.get(Institution, row.institution_id)
    package = db.session.get(Package, row.package_id)
    statuses = []
    authorizations = {
        item.appointment_id: item
        for item in BookingParticipantAuthorization.query.filter(
            BookingParticipantAuthorization.appointment_id.in_(
                [appointment.id for appointment in row.appointments]
            )
        ).all()
    } if row.appointments else {}
    participant_cards = []
    for appointment in row.appointments:
        if appointment.status not in statuses:
            statuses.append(appointment.status)
        authorization = authorizations.get(appointment.id)
        participant_type = (
            authorization.participant_type if authorization else (
                "self"
                if appointment.user_id == row.booked_by_user_id
                else "linked_account"
            )
        )
        external_type = {
            "friend": "linked_account",
            "health_code": "health_code_token",
        }.get(participant_type, participant_type)
        display_name = (
            masked_name(appointment.user)
            if participant_type in {"health_code", "health_code_token"}
            and appointment.user
            else appointment.user_name_snapshot
        )
        participant_card = {
            "id": appointment.id,
            "appointment_id": appointment.id,
            "participant_type": external_type,
            "display_name": display_name,
            "status": appointment.status,
            "status_label": STATUS_LABELS.get(appointment.status, appointment.status),
            "can_cancel": appointment.status == "unfulfilled",
        }
        participant_card.update(_receipt_progress(appointment))
        participant_cards.append(participant_card)
    payload.update({
        "institution": ({"id": institution.id, "name": institution.name,
                         "branch_name": institution.branch_name} if institution else None),
        "package": ({"id": package.id, "name": row.package_name_snapshot or package.name,
                     "domains": row.domain_snapshot or []} if package else None),
        "participants": participant_cards,
        "appointments": participant_cards,
        "participant_names": [item["display_name"] for item in participant_cards],
        "status_codes": statuses,
        "status_labels": [STATUS_LABELS.get(status, status) for status in statuses],
        "can_cancel": any(item.status == "unfulfilled" for item in row.appointments),
    })
    payment_order = PaymentOrder.query.filter_by(booking_group_id=row.id).first()
    payload["payment_order"] = payment_order.to_dict() if payment_order else None
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
            if not user or not user.is_active or not user.profile_completed:
                valid = False; break
            if Appointment.query.filter_by(user_id=user.id, active_date_key=day).first():
                valid = False; break
            if participant.participant_type == "self":
                if user.id != sub.subscriber_user_id:
                    valid = False; break
            elif participant.participant_type in {
                "linked_account",
                "friend",
            }:
                relation = db.session.get(
                    FriendRelation,
                    participant.friend_relation_id,
                )
                if (
                    relation is None
                    or relation.booking_authorization_version
                    != participant.authorization_version
                    or not relation.booking_granted(
                        sub.subscriber_user_id,
                        user.id,
                    )
                ):
                    valid = False; break
            elif participant.participant_type in {
                "health_code_token",
                "health_code",
            }:
                if (
                    not user.allow_health_id_proxy_booking
                    or user.booking_authorization_version
                    != participant.authorization_version
                ):
                    valid = False; break
            else:
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
    from app.services.finance import run_due_finance_tasks
    run_due_finance_tasks()
    db.session.commit()
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


def create_booking_group_for_user(booker, payload, *, commit=True):
    error = profile_completion_error(booker)
    if error:
        return error
    day, error = _parse_day(payload.get("appointment_date"))
    if error: return error
    try: institution_id, package_id = int(payload.get("institution_id")), int(payload.get("package_id"))
    except (TypeError, ValueError): return {"message": "institution_id and package_id must be integers"}, 400
    institution = Institution.query.join(Institution.organization).filter(
        Institution.id == institution_id,
        Institution.is_active.is_(True),
        Institution.operations_suspended_at.is_(None),
        Organization.is_active.is_(True),
    ).first()
    if not institution: return {"message": "institution not found"}, 404
    package, version, result = _package(institution.id, package_id)
    if package is None: return result
    domain_snapshot = result
    participants, error = resolve_booking_participants(booker, payload)
    if error: return error
    intakes, error = participant_intakes(participants)
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
    token_error = consume_participant_tokens(participants)
    if token_error:
        db.session.rollback()
        return token_error
    group = BookingGroup(group_code=f"BG-{uuid.uuid4().hex[:12].upper()}", booked_by_user_id=booker.id,
        institution_id=institution.id, package_id=package.id, package_version_id=version.id,
        appointment_date=day, party_size=len(participants), package_name_snapshot=version.name_snapshot,
        package_price_snapshot=version.price_snapshot, domain_snapshot=domain_snapshot,
        booking_notice_snapshot=version.booking_notice_snapshot, notice_version_snapshot=version.version_number,
        notice_confirmed_at=datetime.now(timezone.utc), contact_snapshot={"email": effective_account_email(booker), "phone": booker.phone})
    db.session.add(group); db.session.flush()
    now = datetime.now(timezone.utc)
    for participant_item in participants:
        participant = participant_item["user"]
        intake = intakes[participant.id]
        appointment = Appointment(user_id=participant.id, booked_by_user_id=booker.id,
            booking_group_id=group.id, institution_id=institution.id, package_id=package.id,
            package_version_id=version.id, appointment_date=day, active_date_key=day, status="pending_payment",
            user_name_snapshot=participant.real_name or participant.username, user_health_id_snapshot=participant.health_id,
            user_birth_date_snapshot=participant.birth_date, user_gender_snapshot=participant.gender,
            user_contact_snapshot=participant.phone or effective_account_email(participant), package_name_snapshot=version.name_snapshot,
            package_price_snapshot=version.price_snapshot,
            height_cm_snapshot=intake["height"], weight_kg_snapshot=intake["weight"],
            bmi_snapshot=intake["bmi"], allergy_history_snapshot=participant.allergy_history,
            medical_history_snapshot=participant.medical_history, intake_captured_at=now)
        db.session.add(appointment); db.session.flush()
        db.session.add(BookingParticipantAuthorization(
            appointment_id=appointment.id,
            booker_user_id=booker.id,
            subject_user_id=participant.id,
            participant_type=participant_item["participant_type"],
            friend_relation_id=participant_item.get("friend_relation_id"),
            authorization_version=participant_item["authorization_version"],
            participant_token_id=participant_item.get("participant_token_id"),
            created_at=now,
        ))
        db.session.add(AppointmentEvent(appointment_id=appointment.id, event_type="order_created",
                                        status_snapshot="pending_payment", message="订单已创建，等待付款",
                                        actor_user_id=booker.id, occurred_at=now))
    slot.revision += 1
    after = _remaining(institution, day)
    _reset_unsatisfied(institution.id, day, after)
    from app.services.finance import create_payment_order
    payment_order = create_payment_order(group, booker, now=now)
    try:
        db.session.commit() if commit else db.session.flush()
    except IntegrityError:
        db.session.rollback()
        conflict = _appointment_conflict_payload(participants, day)
        return conflict or {
            "code": "APPOINTMENT_DATE_CONFLICT",
            "message": "当天已有预约，请先查看或取消原预约后再选择其他日期",
            "appointment_date": day.isoformat(),
            "conflicts": [],
        }, 409
    return {
        "item": _group_payload(group),
        "payment_order": payment_order.to_dict(),
    }, 201


@booking_v7_bp.post("/booking-groups")
@roles_required(ROLE_USER)
def create_group():
    return create_booking_group_for_user(
        g.current_user, request.get_json(silent=True) or {}
    )


def cancel_booking_group_for_user(user, group_id, *, commit=True):
    error = profile_completion_error(user)
    if error:
        return error
    group = BookingGroup.query.filter_by(
        id=group_id,
        booked_by_user_id=user.id,
    ).with_for_update().first()
    if not group: return {"message": "booking group not found"}, 404
    cancellable = Appointment.query.filter(
        Appointment.booking_group_id == group.id,
        Appointment.status == "unfulfilled",
    ).order_by(Appointment.id.asc()).with_for_update().all()
    if not cancellable:
        return {"message": "预约组中没有可取消的预约"}, 409
    institution = db.session.get(Institution, group.institution_id)
    now = datetime.now(timezone.utc)
    appointment_ids = [row.id for row in cancellable]
    try:
        cancelled_count = _cancel_group_appointments_cas(appointment_ids, now)
    except OperationalError:
        db.session.rollback()
        return {
            "message": "appointment group is being updated; reload and retry",
            "code": "APPOINTMENT_STATE_CONFLICT",
        }, 409
    if cancelled_count != len(appointment_ids):
        # A concurrent attendance/closure must not leave a half-cancelled
        # group. Rolling back the guarded UPDATE restores every member.
        db.session.rollback()
        return {
            "message": "appointment group changed; reload and retry",
            "code": "APPOINTMENT_STATE_CONFLICT",
        }, 409
    for row in cancellable:
        db.session.add(AppointmentEvent(appointment_id=row.id, event_type="cancelled",
            status_snapshot="cancelled", message="预约组中的未完成预约已取消",
            actor_user_id=user.id, occurred_at=now))
        from app.models import PaymentOrderItem
        from app.services.finance import refund_item
        payment_item = PaymentOrderItem.query.filter_by(appointment_id=row.id).first()
        if payment_item is not None:
            refund_item(payment_item, actor_user=user, reason="user_cancellation")
    slot = _lock_capacity(institution, group.appointment_date); slot.revision += 1
    enqueue_available(institution, group.appointment_date, slot)
    db.session.commit() if commit else db.session.flush()
    db.session.expire_all()
    group = db.session.get(BookingGroup, group_id)
    return {"item": _group_payload(group)}, 200


def _cancel_group_appointments_cas(appointment_ids, cancelled_at):
    """Cancel an exact snapshot of pending group members in one statement."""
    if not appointment_ids:
        return 0
    result = db.session.execute(
        update(Appointment)
        .where(
            Appointment.id.in_(appointment_ids),
            Appointment.status == "unfulfilled",
        )
        .values(
            status="cancelled",
            active_date_key=None,
            cancelled_at=cancelled_at,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


@booking_v7_bp.post("/booking-groups/<int:group_id>/cancel")
@roles_required(ROLE_USER)
def cancel_group(group_id):
    return cancel_booking_group_for_user(g.current_user, group_id)


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


def create_waitlist_for_user(user, payload, *, commit=True):
    error = profile_completion_error(user)
    if error:
        return error
    day, error = _parse_day(payload.get("appointment_date"))
    if error: return error
    try: institution_id, package_id = int(payload.get("institution_id")), int(payload.get("package_id"))
    except (TypeError, ValueError): return {"message": "institution_id and package_id must be integers"}, 400
    institution = Institution.query.join(Institution.organization).filter(
        Institution.id == institution_id,
        Institution.is_active.is_(True),
        Institution.operations_suspended_at.is_(None),
        Organization.is_active.is_(True),
    ).first()
    if not institution: return {"message": "institution not found"}, 404
    package, version, result = _package(institution.id, package_id)
    if package is None: return result
    participants, error = resolve_booking_participants(user, payload)
    if error: return error
    _intakes, error = participant_intakes(participants)
    if error: return error
    if not effective_account_email(user):
        return {"message": "订阅空位提醒前，请先绑定通知邮箱"}, 400
    _lock_capacity(institution, day)
    remaining = _remaining(institution, day)
    if remaining is None or remaining >= len(participants):
        db.session.rollback()
        return {"message": "当前名额充足，请直接提交正式预约", "code": "BOOK_NOW"}, 409
    existing = WaitlistSubscription.query.filter_by(subscriber_user_id=user.id,
        institution_id=institution.id, package_id=package.id, appointment_date=day,
        party_size=len(participants), status="active").first()
    if existing: db.session.rollback(); return {"message": "equivalent active subscription already exists"}, 409
    token_error = consume_participant_tokens(participants)
    if token_error:
        db.session.rollback()
        return token_error
    sub = WaitlistSubscription(subscriber_user_id=user.id, institution_id=institution.id,
        package_id=package.id, package_version_id=version.id, appointment_date=day,
        party_size=len(participants), notification_email=effective_account_email(user))
    db.session.add(sub); db.session.flush()
    for participant in participants:
        subject = participant["user"]
        db.session.add(WaitlistSubscriptionParticipant(
            subscription_id=sub.id,
            subject_user_id=subject.id,
            name_snapshot=(
                masked_name(subject)
                if participant["participant_type"]
                in {"health_code", "health_code_token"}
                else subject.real_name or subject.username
            ),
            health_id_snapshot=subject.health_id,
            booking_authorized_at=participant["authorized_at"],
            participant_type=participant["participant_type"],
            friend_relation_id=participant.get("friend_relation_id"),
            authorization_version=participant["authorization_version"],
        ))
    db.session.commit() if commit else db.session.flush()
    return {"item": _waitlist_payload(sub)}, 201


@booking_v7_bp.post("/waitlist-subscriptions")
@roles_required(ROLE_USER)
def create_waitlist():
    return create_waitlist_for_user(g.current_user, request.get_json(silent=True) or {})


@booking_v7_bp.delete("/waitlist-subscriptions/<int:subscription_id>")
@roles_required(ROLE_USER)
def cancel_waitlist(subscription_id):
    error = profile_completion_error(g.current_user)
    if error:
        return error
    row = WaitlistSubscription.query.filter_by(id=subscription_id, subscriber_user_id=g.current_user.id).first()
    if not row: return {"message": "waitlist subscription not found"}, 404
    if row.status != "active": return {"message": "waitlist subscription is not active"}, 409
    row.status = "cancelled"; row.closed_at = datetime.now(timezone.utc); db.session.commit()
    return {"item": _waitlist_payload(row)}, 200
