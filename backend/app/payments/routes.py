from flask import g

from app.extensions import db
from app.models import PaymentOrder
from app.payments import payments_bp
from app.services.finance import pay_order, run_due_finance_tasks
from app.services.permissions import ROLE_USER, roles_required


def _owned_order(order_id):
    order = PaymentOrder.query.filter_by(
        id=order_id,
        payer_user_id=g.current_user.id,
    ).first()
    if order is None:
        return None, ({"message": "没有找到该付款订单"}, 404)
    return order, None


@payments_bp.get("/<int:order_id>")
@roles_required(ROLE_USER)
def get_order(order_id):
    run_due_finance_tasks()
    db.session.commit()
    order, error = _owned_order(order_id)
    if error:
        return error
    return {"item": order.to_dict()}, 200


@payments_bp.post("/<int:order_id>/pay")
@roles_required(ROLE_USER)
def pay(order_id):
    order, error = _owned_order(order_id)
    if error:
        return error
    paid, error = pay_order(order, g.current_user)
    if error:
        db.session.commit()
        return error
    db.session.commit()
    return {
        "item": paid.to_dict(),
        "message": "付款成功，预约已正式确认",
    }, 200
