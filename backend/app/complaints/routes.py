from datetime import datetime, timezone

from flask import g, request
from sqlalchemy.exc import IntegrityError

from app.complaints import complaints_bp
from app.extensions import db
from app.models import (
    Appointment,
    AppointmentComplaint,
    ComplaintEvent,
    ComplaintMessage,
    PaymentOrderItem,
    RefundCase,
    User,
)
from app.services.notifications import enqueue_user_notification
from app.services.permissions import ROLE_USER, roles_required
from app.services.user_access import profile_completion_error


COMPLAINT_CATEGORIES = {
    "service",
    "medical_quality",
    "appointment",
    "report",
    "privacy",
    "other",
}


def _paginated(query):
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 15, type=int) or 15, 1), 100)
    total = query.count()
    rows = query.offset((page - 1) * size).limit(size).all()
    return {
        "items": [row.to_dict() for row in rows],
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }


def _owned_complaint(complaint_id):
    item = AppointmentComplaint.query.filter_by(
        id=complaint_id,
        complainant_user_id=g.current_user.id,
    ).first()
    if item is None:
        return None, ({"message": "未找到该投诉记录"}, 404)
    return item, None


def _event(item, event_type, *, content=None):
    db.session.add(ComplaintEvent(
        complaint_id=item.id,
        event_type=event_type,
        actor_user_id=g.current_user.id,
        actor_role=g.current_user.role,
        content=content,
    ))


def _message(item, content, *, created_at=None):
    db.session.add(ComplaintMessage(
        complaint_id=item.id,
        sender_user_id=g.current_user.id,
        sender_role=g.current_user.role,
        content=content,
        created_at=created_at or datetime.now(timezone.utc),
    ))


def _state_conflict(message):
    return {"message": message, "code": "COMPLAINT_STATE_CONFLICT"}, 409


def _transition_owned_complaint_cas(
    item,
    *,
    expected_statuses,
    next_status,
    changed_at,
    values=None,
):
    """Atomically claim a user-owned complaint state transition.

    A prior ownership lookup is useful for the 404 response, but it cannot be
    used as a concurrency guard.  The status predicate below is authoritative:
    only the request whose UPDATE changes one row may append messages, events,
    or notifications in the surrounding transaction.
    """
    update_values = dict(values or {})
    update_values.update({
        AppointmentComplaint.status: next_status,
        AppointmentComplaint.updated_at: changed_at,
    })
    changed = AppointmentComplaint.query.filter(
        AppointmentComplaint.id == item.id,
        AppointmentComplaint.complainant_user_id == g.current_user.id,
        AppointmentComplaint.status.in_(tuple(expected_statuses)),
    ).update(update_values, synchronize_session=False)
    return changed == 1


def _notify_institution(item, *, event_type, title, body):
    administrator = item.institution.administrator if item.institution else None
    if administrator is None or not administrator.is_active:
        return
    enqueue_user_notification(
        administrator,
        event_type=event_type,
        idempotency_key=f"complaint:{item.id}:{event_type}",
        title=title,
        body=body,
        action_url=f"/org/complaints?complaint_id={item.id}",
        payload={"complaint_id": item.id},
    )


def _notify_complainant(item, *, event_type, title, body):
    if item.complainant is None:
        return
    enqueue_user_notification(
        item.complainant,
        event_type=event_type,
        idempotency_key=f"complaint:{item.id}:{event_type}",
        title=title,
        body=body,
        action_url=f"/appointments?complaint_id={item.id}",
        payload={"complaint_id": item.id},
    )


def _notify_platform(item):
    admins = User.query.filter_by(role="admin", is_active=True).all()
    for administrator in admins:
        enqueue_user_notification(
            administrator,
            event_type="complaint_escalated",
            idempotency_key=f"complaint:{item.id}:escalated:admin:{administrator.id}",
            title="用户投诉申请平台介入",
            body=f"投诉 #{item.id} 已由用户升级，请及时开始处理。",
            action_url=f"/admin/complaints?complaint_id={item.id}",
            payload={"complaint_id": item.id},
        )


@complaints_bp.get("")
@roles_required(ROLE_USER)
def list_my_complaints():
    query = AppointmentComplaint.query.filter_by(
        complainant_user_id=g.current_user.id,
    )
    status = str(request.args.get("status") or "").strip()
    if status:
        query = query.filter_by(status=status)
    query = query.order_by(
        AppointmentComplaint.updated_at.desc(),
        AppointmentComplaint.id.desc(),
    )
    return _paginated(query), 200


@complaints_bp.post("")
@roles_required(ROLE_USER)
def create_complaint():
    identity_error = profile_completion_error(g.current_user)
    if identity_error:
        return identity_error
    payload = request.get_json(silent=True) or {}
    try:
        appointment_id = int(payload.get("appointment_id"))
    except (TypeError, ValueError):
        return {"message": "appointment_id is required"}, 400
    appointment = Appointment.query.filter(
        Appointment.id == appointment_id,
        Appointment.booked_by_user_id == g.current_user.id,
    ).first()
    if appointment is None:
        return {"message": "只有原付款人可以发起投诉与退款"}, 404
    payment_item = PaymentOrderItem.query.filter_by(appointment_id=appointment.id).first()
    if (
        payment_item is None
        or payment_item.order.payer_user_id != g.current_user.id
        or payment_item.order.status in {"pending", "expired"}
        or payment_item.fund_status == "refunded"
    ):
        return {"message": "该预约没有可申请退款的已付款订单"}, 409
    category = str(payload.get("category") or "service").strip()
    if category not in COMPLAINT_CATEGORIES:
        return {"message": "投诉类型不受支持"}, 400
    content = str(payload.get("content") or "").strip()
    if not content:
        return {"message": "请填写投诉详情"}, 400
    if len(content) > 2000:
        return {"message": "投诉详情不能超过2000个字符"}, 400
    if AppointmentComplaint.query.filter_by(appointment_id=appointment.id).first():
        return {
            "message": "该预约已有投诉记录，请在原投诉中继续处理",
            "code": "COMPLAINT_ALREADY_EXISTS",
        }, 409
    item = AppointmentComplaint(
        appointment_id=appointment.id,
        institution_id=appointment.institution_id,
        complainant_user_id=g.current_user.id,
        complainant_username_snapshot=g.current_user.username,
        category=category,
        content=content,
        status="institution_pending",
    )
    db.session.add(item)
    try:
        db.session.flush()
        db.session.add(RefundCase(
            complaint_id=item.id,
            payment_item_id=payment_item.id,
            requested_by_user_id=g.current_user.id,
            status="requested",
        ))
        _message(item, content, created_at=item.created_at)
        _event(item, "created", content=content)
        _notify_institution(
            item,
            event_type="complaint_created",
            title="收到新的预约投诉",
            body=f"预约 #{appointment.id} 收到用户投诉，请及时处理回复。",
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {
            "message": "该预约已有投诉记录，请刷新后查看",
            "code": "COMPLAINT_ALREADY_EXISTS",
        }, 409
    return {"item": item.to_dict(), "message": "投诉已提交，等待机构处理"}, 201


@complaints_bp.get("/<int:complaint_id>")
@roles_required(ROLE_USER)
def get_my_complaint(complaint_id):
    item, error = _owned_complaint(complaint_id)
    if error:
        return error
    return {"item": item.to_dict()}, 200


@complaints_bp.post("/<int:complaint_id>/confirm-resolved")
@roles_required(ROLE_USER)
def confirm_complaint_resolved(complaint_id):
    identity_error = profile_completion_error(g.current_user)
    if identity_error:
        return identity_error
    item, error = _owned_complaint(complaint_id)
    if error:
        return error
    if item.status != "user_confirmation":
        return _state_conflict("当前投诉不处于待用户确认状态")
    now = datetime.now(timezone.utc)
    if not _transition_owned_complaint_cas(
        item,
        expected_statuses={"user_confirmation"},
        next_status="resolved",
        changed_at=now,
        values={AppointmentComplaint.resolved_at: now},
    ):
        db.session.rollback()
        return _state_conflict("投诉状态已变化，请刷新后重试")
    _event(item, "user_confirmed", content="用户确认机构处理结果，投诉已解决")
    if item.refund_case and item.refund_case.status == "requested":
        item.refund_case.status = "denied"
        item.refund_case.decision = "user_accepted_no_refund"
        item.refund_case.decided_at = now
    _notify_institution(
        item,
        event_type="complaint_user_confirmed",
        title="用户已确认投诉解决",
        body=f"投诉 #{item.id} 已由用户确认解决。",
    )
    db.session.commit()
    return {"item": item.to_dict(), "message": "已确认投诉解决"}, 200


@complaints_bp.post("/<int:complaint_id>/confirm")
@roles_required(ROLE_USER)
def legacy_confirm_complaint_resolved(complaint_id):
    return confirm_complaint_resolved.__wrapped__(complaint_id)


@complaints_bp.post("/<int:complaint_id>/escalate")
@roles_required(ROLE_USER)
def escalate_complaint(complaint_id):
    identity_error = profile_completion_error(g.current_user)
    if identity_error:
        return identity_error
    item, error = _owned_complaint(complaint_id)
    if error:
        return error
    if item.status not in {"institution_pending", "user_confirmation"}:
        return _state_conflict("当前投诉状态不能申请平台介入")
    reason = str((request.get_json(silent=True) or {}).get("reason") or "").strip()
    if not reason:
        return {"message": "请说明对机构处理结果不满意的原因"}, 400
    if len(reason) > 2000:
        return {"message": "申请平台介入原因不能超过2000个字符"}, 400
    escalated_at = datetime.now(timezone.utc)
    if not _transition_owned_complaint_cas(
        item,
        expected_statuses={item.status},
        next_status="platform_pending",
        changed_at=escalated_at,
        values={
            AppointmentComplaint.escalation_reason: reason,
            AppointmentComplaint.escalated_at: escalated_at,
        },
    ):
        db.session.rollback()
        return _state_conflict("投诉状态已变化，请刷新后重试")
    _message(item, reason, created_at=escalated_at)
    _event(item, "escalated", content=reason)
    _notify_institution(
        item,
        event_type="complaint_escalated",
        title="投诉已升级平台处理",
        body=f"投诉 #{item.id} 已由用户申请平台介入，机构端处理权限已关闭。",
    )
    _notify_platform(item)
    db.session.commit()
    return {"item": item.to_dict(), "message": "已申请平台管理员介入"}, 200
