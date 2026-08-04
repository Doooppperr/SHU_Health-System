from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import uuid

from sqlalchemy import func, update

from app.extensions import db
from app.models import (
    Appointment,
    AppointmentComplaint,
    AppointmentEvent,
    FinanceLedgerEntry,
    FinanceTransaction,
    Institution,
    NotificationOutbox,
    PaymentOrder,
    PaymentOrderItem,
    RefundCase,
    WaitlistSubscription,
)
from app.services.notifications import enqueue_user_notification


FEE_RATE = Decimal("0.025000")
MONEY_QUANTUM = Decimal("0.01")
PAYMENT_HOLD_MINUTES = 15
SETTLEMENT_DAYS = 7
REFUND_ACTION_HOURS = 72


def utc_now():
    return datetime.now(timezone.utc)


def comparable_now(value, now):
    if value is not None and value.tzinfo is None:
        return now.replace(tzinfo=None)
    return now


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def split_amount(gross) -> tuple[Decimal, Decimal, Decimal]:
    gross_amount = money(gross)
    fee = (gross_amount * FEE_RATE).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    return gross_amount, fee, gross_amount - fee


def _transaction(
    *,
    transaction_type,
    idempotency_key,
    item,
    entries,
    actor_user_id=None,
    complaint_id=None,
    occurred_at=None,
):
    existing = FinanceTransaction.query.filter_by(idempotency_key=idempotency_key).first()
    if existing is not None:
        return existing, False
    occurred_at = occurred_at or utc_now()
    row = FinanceTransaction(
        transaction_no=f"FT-{occurred_at:%Y%m%d}-{uuid.uuid4().hex[:14].upper()}",
        transaction_type=transaction_type,
        idempotency_key=idempotency_key,
        payment_item_id=item.id,
        actor_user_id=actor_user_id,
        complaint_id=complaint_id,
        gross_amount=item.gross_amount,
        fee_amount=item.fee_amount,
        net_amount=item.net_amount,
        created_at=occurred_at,
    )
    db.session.add(row)
    db.session.flush()
    for account_type, institution_id, amount in entries:
        db.session.add(FinanceLedgerEntry(
            transaction_id=row.id,
            account_type=account_type,
            institution_id=institution_id,
            amount=money(amount),
            created_at=occurred_at,
        ))
    return row, True


def create_payment_order(group, payer, *, now=None):
    now = now or utc_now()
    order = PaymentOrder(
        order_no=f"HD{now:%Y%m%d%H%M%S}{uuid.uuid4().hex[:8].upper()}",
        booking_group_id=group.id,
        payer_user_id=payer.id,
        amount=money(sum((appointment.package_price_snapshot for appointment in group.appointments), Decimal("0"))),
        status="pending",
        source="online",
        expires_at=now + timedelta(minutes=PAYMENT_HOLD_MINUTES),
        created_at=now,
        updated_at=now,
    )
    db.session.add(order)
    db.session.flush()
    for appointment in group.appointments:
        gross, fee, net = split_amount(appointment.package_price_snapshot)
        db.session.add(PaymentOrderItem(
            order_id=order.id,
            appointment_id=appointment.id,
            institution_id=appointment.institution_id,
            gross_amount=gross,
            fee_rate=FEE_RATE,
            fee_amount=fee,
            net_amount=net,
            fund_status="pending",
            created_at=now,
        ))
    db.session.flush()
    return order


def backfill_historical_settlements(*, now=None):
    """Create explicit paid-and-settled records for pre-v13 fulfilled visits."""
    now = now or utc_now()
    created = 0
    appointments = Appointment.query.filter_by(status="fulfilled").order_by(Appointment.id).all()
    for appointment in appointments:
        if PaymentOrderItem.query.filter_by(appointment_id=appointment.id).first() is not None:
            continue
        payer_id = appointment.booked_by_user_id or appointment.user_id
        gross, fee, net = split_amount(appointment.package_price_snapshot)
        order = PaymentOrder(
            order_no=f"HIST{appointment.id:08d}{uuid.uuid4().hex[:6].upper()}",
            booking_group_id=None,
            payer_user_id=payer_id,
            amount=gross,
            status="paid",
            source="historical",
            paid_at=now,
            created_at=appointment.created_at or now,
            updated_at=now,
        )
        db.session.add(order)
        db.session.flush()
        item = PaymentOrderItem(
            order_id=order.id,
            appointment_id=appointment.id,
            institution_id=appointment.institution_id,
            gross_amount=gross,
            fee_rate=FEE_RATE,
            fee_amount=fee,
            net_amount=net,
            fund_status="held",
            created_at=appointment.created_at or now,
        )
        db.session.add(item)
        db.session.flush()
        _transaction(
            transaction_type="historical_payment",
            idempotency_key=f"payment-item:{item.id}:received",
            item=item,
            occurred_at=now,
            entries=[("platform_custody", None, item.gross_amount)],
        )
        settle_item(item, now=now, historical=True)
        created += 1
    return created


def _order_status_after_refund(order, *, now):
    statuses = {item.fund_status for item in order.items}
    if statuses == {"refunded"}:
        order.status = "refunded"
        order.refunded_at = now
    elif "refunded" in statuses:
        order.status = "partially_refunded"


def pay_order(order, payer, *, now=None):
    now = now or utc_now()
    if order.payer_user_id != payer.id:
        return None, ({"message": "没有找到该付款订单"}, 404)
    if order.status in {"paid", "partially_refunded", "refunded"}:
        return order, None
    if order.status == "expired" or (
        order.expires_at and order.expires_at <= comparable_now(order.expires_at, now)
    ):
        expire_payment_order(order, now=now)
        return None, ({"message": "付款时间已结束，请重新预约", "code": "PAYMENT_EXPIRED"}, 409)
    changed = PaymentOrder.query.filter(
        PaymentOrder.id == order.id,
        PaymentOrder.status == "pending",
        PaymentOrder.expires_at > now,
    ).update({
        PaymentOrder.status: "paid",
        PaymentOrder.paid_at: now,
        PaymentOrder.updated_at: now,
    }, synchronize_session=False)
    if changed != 1:
        db.session.rollback()
        return None, ({"message": "订单状态已变化，请刷新后重试", "code": "PAYMENT_STATE_CONFLICT"}, 409)
    db.session.expire(order)
    for item in order.items:
        if item.fund_status != "pending":
            continue
        item.fund_status = "held"
        _transaction(
            transaction_type="payment_received",
            idempotency_key=f"payment-item:{item.id}:received",
            item=item,
            actor_user_id=payer.id,
            occurred_at=now,
            entries=[("platform_custody", None, item.gross_amount)],
        )
        appointment = item.appointment
        if appointment.status == "pending_payment":
            appointment.status = "unfulfilled"
            db.session.add(AppointmentEvent(
                appointment_id=appointment.id,
                event_type="payment_completed",
                status_snapshot="unfulfilled",
                message="付款完成，预约正式确认",
                actor_user_id=payer.id,
                occurred_at=now,
            ))
    db.session.flush()
    enqueue_paid_booking_notifications(order)
    return order, None


def enqueue_paid_booking_notifications(order):
    group = order.booking_group
    if group is None:
        return
    institution = group.institution
    recipient = institution.notification_email if institution else None
    if institution and institution.notification_enabled and recipient:
        existing = NotificationOutbox.query.filter_by(
            idempotency_key=f"booking-group:{group.id}:paid"
        ).first()
        if existing is None:
            db.session.add(NotificationOutbox(
                event_type="booking_group_created",
                idempotency_key=f"booking-group:{group.id}:paid",
                recipient=recipient,
                payload={
                    "group_code": group.group_code,
                    "institution": institution.name,
                    "branch": institution.branch_name,
                    "appointment_date": group.appointment_date.isoformat(),
                    "package": group.package_name_snapshot,
                    "party_size": group.party_size,
                    "login_url": "/org/reports",
                },
            ))
        from app.booking_v7.routes import _remaining
        if _remaining(institution, group.appointment_date) == 0:
            full_key = f"institution:{institution.id}:date:{group.appointment_date.isoformat()}:full:paid:{order.id}"
            if NotificationOutbox.query.filter_by(idempotency_key=full_key).first() is None:
                db.session.add(NotificationOutbox(
                    event_type="appointment_date_full",
                    idempotency_key=full_key,
                    recipient=recipient,
                    payload={
                        "institution": institution.name,
                        "appointment_date": group.appointment_date.isoformat(),
                        "message": "该日期预约名额已满",
                        "login_url": "/org/dashboard",
                    },
                ))
    users = {appointment.user_id: appointment.user for appointment in group.appointments}
    if order.payer:
        users[order.payer.id] = order.payer
    participant_names = "、".join(
        appointment.user_name_snapshot for appointment in group.appointments
    )
    for user in users.values():
        if user is None:
            continue
        visible_names = (
            participant_names
            if user.id == order.payer_user_id
            else next(
                (
                    appointment.user_name_snapshot
                    for appointment in group.appointments
                    if appointment.user_id == user.id
                ),
                "本次受检者",
            )
        )
        enqueue_user_notification(
            user,
            event_type="booking_user_confirmed",
            idempotency_key=f"booking-group:{group.id}:user:{user.id}:paid",
            title="付款成功，预约已确认",
            body=(
                f"订单 {order.order_no} 已付款 ¥{money(order.amount)}。"
                f"{group.appointment_date.isoformat()} {institution.name}·{institution.branch_name}，"
                f"套餐：{group.package_name_snapshot}，受检者：{visible_names}。"
            ),
            action_url="/appointments",
            payload={"booking_group_id": group.id, "payment_order_id": order.id},
            email_payload={
                "group_code": group.group_code,
                "order_no": order.order_no,
                "amount": f"{money(order.amount):.2f}",
                "institution": institution.name,
                "branch": institution.branch_name,
                "address": institution.address,
                "consult_phone": institution.consult_phone,
                "appointment_date": group.appointment_date.isoformat(),
                "package": group.package_name_snapshot,
                "recipient_name": user.real_name or "用户",
                "participants": [{"name": name} for name in visible_names.split("、")],
                "preparation_items": ["身份证原件", "HealthDoc 预约凭证", "病历本", "既往报告和用药清单"],
                "login_url": "/appointments",
            },
        )
    for subscription in WaitlistSubscription.query.filter_by(
        subscriber_user_id=order.payer_user_id,
        institution_id=group.institution_id,
        package_id=group.package_id,
        appointment_date=group.appointment_date,
        party_size=group.party_size,
        status="active",
    ).all():
        subscription.status = "closed"
        subscription.closed_at = order.paid_at


def expire_payment_order(order, *, now=None):
    now = now or utc_now()
    if order.status != "pending":
        return False
    changed = PaymentOrder.query.filter_by(id=order.id, status="pending").update({
        PaymentOrder.status: "expired",
        PaymentOrder.expired_at: now,
        PaymentOrder.updated_at: now,
    }, synchronize_session=False)
    if changed != 1:
        return False
    institution_dates = set()
    for item in order.items:
        item.fund_status = "pending"
        appointment = item.appointment
        if appointment.status == "pending_payment":
            appointment.status = "payment_expired"
            appointment.active_date_key = None
            institution_dates.add((appointment.institution_id, appointment.appointment_date))
            db.session.add(AppointmentEvent(
                appointment_id=appointment.id,
                event_type="payment_expired",
                status_snapshot="payment_expired",
                message="付款时间结束，预约名额已释放",
                actor_user_id=order.payer_user_id,
                occurred_at=now,
            ))
    if institution_dates:
        from app.booking_v7.routes import _lock_capacity, enqueue_available
        for institution_id, appointment_date in institution_dates:
            appointment = next(
                item.appointment for item in order.items
                if item.institution_id == institution_id and item.appointment.appointment_date == appointment_date
            )
            slot = _lock_capacity(appointment.institution, appointment_date)
            slot.revision += 1
            enqueue_available(appointment.institution, appointment_date, slot)
    return True


def schedule_settlement_for_appointment(appointment, *, published_at=None):
    item = PaymentOrderItem.query.filter_by(appointment_id=appointment.id).first()
    if item is None or item.fund_status != "held":
        return item
    published_at = published_at or utc_now()
    item.fund_status = "scheduled"
    item.settlement_due_at = published_at + timedelta(days=SETTLEMENT_DAYS)
    return item


def _complaint_blocks_settlement(item):
    complaint = AppointmentComplaint.query.filter_by(appointment_id=item.appointment_id).first()
    return bool(complaint and complaint.status != "resolved")


def settle_item(item, *, now=None, historical=False):
    now = now or utc_now()
    if item.fund_status == "settled":
        return False
    if not historical and item.fund_status != "scheduled":
        return False
    if not historical and (
        not item.settlement_due_at
        or item.settlement_due_at > comparable_now(item.settlement_due_at, now)
        or _complaint_blocks_settlement(item)
    ):
        return False
    if not historical:
        changed = PaymentOrderItem.query.filter(
            PaymentOrderItem.id == item.id,
            PaymentOrderItem.fund_status == "scheduled",
            PaymentOrderItem.settlement_due_at <= now,
        ).update({
            PaymentOrderItem.fund_status: "settled",
            PaymentOrderItem.settled_at: now,
        }, synchronize_session=False)
        if changed != 1:
            return False
    _transaction(
        transaction_type="historical_settlement" if historical else "settlement",
        idempotency_key=f"payment-item:{item.id}:settled",
        item=item,
        occurred_at=now,
        entries=[
            ("platform_custody", None, -item.gross_amount),
            ("platform_fee", None, item.fee_amount),
            ("institution_available", item.institution_id, item.net_amount),
        ],
    )
    item.fund_status = "settled"
    item.settled_at = now
    return True


def refund_item(item, *, actor_user=None, complaint=None, now=None, reason="refund"):
    now = now or utc_now()
    if item.fund_status == "refunded":
        return False
    if item.fund_status in {"held", "scheduled"}:
        entries = [("platform_custody", None, -item.gross_amount)]
    elif item.fund_status in {"settled", "refund_required"}:
        entries = [
            ("institution_available", item.institution_id, -item.net_amount),
            ("platform_fee", None, -item.fee_amount),
        ]
    else:
        return False
    previous_status = item.fund_status
    changed = PaymentOrderItem.query.filter(
        PaymentOrderItem.id == item.id,
        PaymentOrderItem.fund_status == previous_status,
    ).update({
        PaymentOrderItem.fund_status: "refunded",
        PaymentOrderItem.refunded_at: now,
        PaymentOrderItem.refund_due_at: None,
    }, synchronize_session=False)
    if changed != 1:
        return False
    _transaction(
        transaction_type="refund",
        idempotency_key=f"payment-item:{item.id}:refunded",
        item=item,
        actor_user_id=actor_user.id if actor_user else None,
        complaint_id=complaint.id if complaint else None,
        occurred_at=now,
        entries=entries,
    )
    item.fund_status = "refunded"
    item.refunded_at = now
    item.refund_due_at = None
    if item.refund_case:
        item.refund_case.status = "refunded"
        item.refund_case.refunded_at = now
        item.refund_case.updated_at = now
    _order_status_after_refund(item.order, now=now)
    payer = item.order.payer
    if payer:
        enqueue_user_notification(
            payer,
            event_type="payment_refunded",
            idempotency_key=f"payment-item:{item.id}:refunded",
            title="退款已原路退回",
            body=f"订单 {item.order.order_no} 的 ¥{money(item.gross_amount)} 已按照原付款路径退回。",
            action_url="/appointments",
            payload={"order_id": item.order_id, "appointment_id": item.appointment_id},
            email_payload={
                "recipient_name": payer.real_name or "用户",
                "order_no": item.order.order_no,
                "amount": f"{money(item.gross_amount):.2f}",
                "message": f"订单 {item.order.order_no} 的退款已按照原付款路径退回。",
                "login_url": "/appointments",
            },
        )
    restore_institution_if_clear(item.institution, now=now)
    return True


def require_institution_refund(refund_case, *, decided_by, note=None, now=None):
    now = now or utc_now()
    item = refund_case.payment_item
    refund_case.status = "institution_action_required"
    refund_case.decision = "institution_fault_refund"
    refund_case.decision_note = note
    refund_case.decided_by_user_id = decided_by.id
    refund_case.decided_at = now
    refund_case.due_at = now + timedelta(hours=REFUND_ACTION_HOURS)
    refund_case.updated_at = now
    item.fund_status = "refund_required"
    item.refund_required_at = now
    item.refund_due_at = refund_case.due_at
    administrator = item.institution.administrator
    if administrator:
        enqueue_user_notification(
            administrator,
            event_type="institution_refund_required",
            idempotency_key=f"refund-case:{refund_case.id}:required",
            title="请在三天内完成订单退款",
            body=f"订单 {item.order.order_no} 已认定由机构承担责任，请在 {refund_case.due_at.isoformat()} 前完成退款。",
            action_url=f"/org/finance?item_id={item.id}",
            payload={"refund_case_id": refund_case.id, "payment_item_id": item.id},
            email_payload={
                "institution": item.institution.name,
                "branch": item.institution.branch_name,
                "order_no": item.order.order_no,
                "amount": f"{money(item.gross_amount):.2f}",
                "deadline": refund_case.due_at.isoformat(),
                "message": f"订单 {item.order.order_no} 需在三天内完成退款，否则分院运营将暂停。",
                "login_url": "/org/finance",
            },
        )


def suspend_overdue_institutions(*, now=None):
    now = now or utc_now()
    items = PaymentOrderItem.query.filter(
        PaymentOrderItem.fund_status == "refund_required",
        PaymentOrderItem.refund_due_at <= now,
    ).all()
    changed = 0
    for item in items:
        institution = item.institution
        if institution.operations_suspended_at is None:
            institution.operations_suspended_at = now
            institution.operations_suspension_reason = "存在超过三天未完成的订单退款"
            administrator = institution.administrator
            if administrator:
                enqueue_user_notification(
                    administrator,
                    event_type="institution_operations_suspended",
                    idempotency_key=f"institution:{institution.id}:suspended:refund:{item.id}",
                    title="分院运营已暂停",
                    body="因订单退款超过处理期限，新预约及日常运营已暂停。完成全部逾期退款后将自动恢复。",
                    action_url="/org/finance",
                    payload={"payment_item_id": item.id},
                )
            changed += 1
    return changed


def restore_institution_if_clear(institution, *, now=None):
    if institution is None or institution.operations_suspended_at is None:
        return False
    overdue = PaymentOrderItem.query.filter(
        PaymentOrderItem.institution_id == institution.id,
        PaymentOrderItem.fund_status == "refund_required",
    ).count()
    if overdue:
        return False
    institution.operations_suspended_at = None
    institution.operations_suspension_reason = None
    administrator = institution.administrator
    if administrator:
        enqueue_user_notification(
            administrator,
            event_type="institution_operations_restored",
            idempotency_key=f"institution:{institution.id}:restored:{int((now or utc_now()).timestamp())}",
            title="分院运营已恢复",
            body="所有逾期退款均已完成，平台运营权限已自动恢复。",
            action_url="/org/dashboard",
        )
    return True


def run_due_finance_tasks(*, now=None):
    now = now or utc_now()
    expired = 0
    settled = 0
    for order in PaymentOrder.query.filter(
        PaymentOrder.status == "pending",
        PaymentOrder.expires_at <= now,
    ).with_for_update().all():
        expired += int(expire_payment_order(order, now=now))
    for item in PaymentOrderItem.query.filter(
        PaymentOrderItem.fund_status == "scheduled",
        PaymentOrderItem.settlement_due_at <= now,
    ).with_for_update().all():
        settled += int(settle_item(item, now=now))
    suspended = suspend_overdue_institutions(now=now)
    return {"expired": expired, "settled": settled, "suspended": suspended}


def account_balance(account_type, *, institution_id=None):
    query = db.session.query(func.coalesce(func.sum(FinanceLedgerEntry.amount), 0)).filter(
        FinanceLedgerEntry.account_type == account_type,
    )
    if institution_id is None:
        query = query.filter(FinanceLedgerEntry.institution_id.is_(None))
    else:
        query = query.filter(FinanceLedgerEntry.institution_id == institution_id)
    return money(query.scalar())


def institution_finance_summary(institution):
    available = account_balance("institution_available", institution_id=institution.id)
    credited = money(db.session.query(func.coalesce(func.sum(PaymentOrderItem.net_amount), 0)).filter(
        PaymentOrderItem.institution_id == institution.id,
        PaymentOrderItem.settled_at.is_not(None),
    ).scalar())
    refunded = money(db.session.query(func.coalesce(func.sum(PaymentOrderItem.net_amount), 0)).filter(
        PaymentOrderItem.institution_id == institution.id,
        PaymentOrderItem.refunded_at.is_not(None),
        PaymentOrderItem.settled_at.is_not(None),
    ).scalar())
    pending = money(db.session.query(func.coalesce(func.sum(PaymentOrderItem.net_amount), 0)).filter(
        PaymentOrderItem.institution_id == institution.id,
        PaymentOrderItem.fund_status.in_(("held", "scheduled")),
    ).scalar())
    required = PaymentOrderItem.query.filter_by(
        institution_id=institution.id,
        fund_status="refund_required",
    ).count()
    return {
        "available_balance": float(available),
        "cumulative_credited": float(credited),
        "pending_settlement": float(pending),
        "cumulative_refunded": float(refunded),
        "refund_required_count": required,
        "operations_suspended": institution.operations_suspended_at is not None,
        "operations_suspended_at": institution.operations_suspended_at.isoformat() if institution.operations_suspended_at else None,
        "operations_suspension_reason": institution.operations_suspension_reason,
    }


def platform_finance_summary():
    pending = money(db.session.query(func.coalesce(func.sum(PaymentOrderItem.net_amount), 0)).filter(
        PaymentOrderItem.fund_status.in_(("held", "scheduled")),
    ).scalar())
    required = PaymentOrderItem.query.filter_by(fund_status="refund_required").count()
    suspended = Institution.query.filter(Institution.operations_suspended_at.is_not(None)).count()
    return {
        "platform_custody": float(account_balance("platform_custody")),
        "platform_fee": float(account_balance("platform_fee")),
        "pending_settlement": float(pending),
        "refund_required_count": required,
        "suspended_institution_count": int(suspended or 0),
    }
