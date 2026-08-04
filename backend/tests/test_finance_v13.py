from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.extensions import db
from app.models import (
    Appointment,
    FinanceLedgerEntry,
    FinanceTransaction,
    Institution,
    Package,
    PaymentOrder,
    PaymentOrderItem,
    RefundCase,
)
from app.services.finance import (
    account_balance,
    backfill_historical_settlements,
    create_payment_order,
    money,
    refund_item,
    run_due_finance_tasks,
    settle_item,
    split_amount,
)


PASSWORD = "Shuhealthdoc！"


def login(client, username):
    response = client.post("/api/auth/login", json=client.login_payload(username, PASSWORD))
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def create_order(app, client, *, username="test1", days=3):
    headers = login(client, username)
    with app.app_context():
        institution = Institution.query.filter_by(is_active=True).order_by(Institution.id).first()
        package = Package.query.filter_by(institution_id=institution.id, is_active=True).first()
        institution_id, package_id = institution.id, package.id
    response = client.post("/api/appointments", headers=headers, json={
        "institution_id": institution_id,
        "package_id": package_id,
        "appointment_date": (date.today() + timedelta(days=days)).isoformat(),
        "height_cm": 170,
        "weight_kg": 65,
        "notice_confirmed": True,
    })
    assert response.status_code == 201, response.get_json()
    return headers, response.get_json()


def test_payment_uses_per_item_half_up_fee_and_is_idempotent(app, client):
    headers, created = create_order(app, client)
    order = created["payment_order"]
    assert order["status"] == "pending"
    assert created["item"]["status"] == "pending_payment"

    paid = client.post(f"/api/payment-orders/{order['id']}/pay", headers=headers)
    assert paid.status_code == 200, paid.get_json()
    repeated = client.post(f"/api/payment-orders/{order['id']}/pay", headers=headers)
    assert repeated.status_code == 200
    with app.app_context():
        row = db.session.get(PaymentOrder, order["id"])
        item = row.items[0]
        gross, fee, net = split_amount(item.gross_amount)
        assert item.fund_status == "held"
        assert item.appointment.status == "unfulfilled"
        assert gross == money(item.gross_amount)
        assert fee == money(gross * Decimal("0.025"))
        assert net == gross - fee
        assert FinanceLedgerEntry.query.filter_by(account_type="platform_custody").count() == 1


def test_paid_cancellation_refunds_full_amount_and_releases_custody(app, client):
    headers, created = create_order(app, client, days=4)
    order_id = created["payment_order"]["id"]
    appointment_id = created["item"]["id"]
    assert client.post(f"/api/payment-orders/{order_id}/pay", headers=headers).status_code == 200
    cancelled = client.post(f"/api/appointments/{appointment_id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.get_json()
    assert client.post(
        f"/api/appointments/{appointment_id}/cancel",
        headers=headers,
    ).status_code == 409
    with app.app_context():
        item = PaymentOrderItem.query.filter_by(appointment_id=appointment_id).one()
        assert item.fund_status == "refunded"
        assert item.order.status == "refunded"
        assert account_balance("platform_custody") == Decimal("0.00")
        assert FinanceTransaction.query.count() == 2


def test_due_settlement_moves_custody_to_fee_and_institution(app, client):
    headers, created = create_order(app, client, days=5)
    order_id = created["payment_order"]["id"]
    assert client.post(f"/api/payment-orders/{order_id}/pay", headers=headers).status_code == 200
    with app.app_context():
        item = db.session.get(PaymentOrder, order_id).items[0]
        item.fund_status = "scheduled"
        item.settlement_due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()
        result = run_due_finance_tasks()
        db.session.commit()
        assert result["settled"] == 1
        assert item.fund_status == "settled"
        assert account_balance("platform_custody") == Decimal("0.00")
        assert account_balance("platform_fee") == money(item.fee_amount)
        assert account_balance("institution_available", institution_id=item.institution_id) == money(item.net_amount)


def test_expired_payment_cannot_race_into_paid_state_and_releases_appointment(app, client):
    payer, created = create_order(app, client, days=10)
    order_id = created["payment_order"]["id"]
    appointment_id = created["item"]["id"]
    with app.app_context():
        order = db.session.get(PaymentOrder, order_id)
        order.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()
        result = run_due_finance_tasks()
        db.session.commit()
        assert result["expired"] == 1
        appointment = db.session.get(Appointment, appointment_id)
        assert appointment.status == "payment_expired"
        assert appointment.active_date_key is None
    rejected = client.post(f"/api/payment-orders/{order_id}/pay", headers=payer)
    assert rejected.status_code == 409
    assert rejected.get_json()["code"] == "PAYMENT_EXPIRED"


def test_overdue_refund_suspends_and_completed_refund_restores(app, client):
    headers, created = create_order(app, client, days=6)
    order_id = created["payment_order"]["id"]
    assert client.post(f"/api/payment-orders/{order_id}/pay", headers=headers).status_code == 200
    with app.app_context():
        item = db.session.get(PaymentOrder, order_id).items[0]
        settle_item(item, historical=True)
        item.fund_status = "refund_required"
        item.refund_due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()
        result = run_due_finance_tasks()
        db.session.commit()
        assert result["suspended"] == 1
        assert item.institution.operations_suspended_at is not None
        refund_item(item)
        db.session.commit()
        assert item.institution.operations_suspended_at is None


def test_only_original_payer_can_open_case_and_institution_can_refund(app, client):
    payer, created = create_order(app, client, days=7)
    appointment_id = created["item"]["id"]
    order_id = created["payment_order"]["id"]
    assert client.post(f"/api/payment-orders/{order_id}/pay", headers=payer).status_code == 200
    other = login(client, "test2")
    denied = client.post("/api/complaints", headers=other, json={
        "appointment_id": appointment_id,
        "category": "service",
        "content": "申请核验本次服务并退款",
    })
    assert denied.status_code == 404
    opened = client.post("/api/complaints", headers=payer, json={
        "appointment_id": appointment_id,
        "category": "service",
        "content": "申请核验本次服务并退款",
    })
    assert opened.status_code == 201, opened.get_json()
    complaint_id = opened.get_json()["item"]["id"]
    institution = login(client, "institution1_staff1")
    refunded = client.post(
        f"/api/org/complaints/{complaint_id}/approve-refund",
        headers=institution,
    )
    assert refunded.status_code == 200, refunded.get_json()
    with app.app_context():
        item = PaymentOrderItem.query.filter_by(appointment_id=appointment_id).one()
        assert item.fund_status == "refunded"
        assert item.refund_case.status == "refunded"
        assert account_balance("platform_custody") == Decimal("0.00")


def test_platform_award_after_settlement_sets_exact_72_hour_obligation(app, client):
    payer, created = create_order(app, client, days=8)
    appointment_id = created["item"]["id"]
    order_id = created["payment_order"]["id"]
    assert client.post(f"/api/payment-orders/{order_id}/pay", headers=payer).status_code == 200
    with app.app_context():
        item = PaymentOrderItem.query.filter_by(appointment_id=appointment_id).one()
        settle_item(item, historical=True)
        db.session.commit()
    opened = client.post("/api/complaints", headers=payer, json={
        "appointment_id": appointment_id,
        "category": "service",
        "content": "服务未按约定完成，申请平台核验退款",
    })
    complaint_id = opened.get_json()["item"]["id"]
    assert client.post(
        f"/api/complaints/{complaint_id}/escalate",
        headers=payer,
        json={"reason": "机构处理结果不能解决资金问题"},
    ).status_code == 200
    admin_login = client.post(
        "/api/auth/login",
        json=client.login_payload("admin", "admin123"),
    )
    assert admin_login.status_code == 200
    admin = {"Authorization": f"Bearer {admin_login.get_json()['access_token']}"}
    assert client.post(f"/api/admin/complaints/{complaint_id}/start", headers=admin).status_code == 200
    assert client.post(
        f"/api/admin/complaints/{complaint_id}/reply",
        headers=admin,
        json={"content": "平台已核验订单、到检与沟通记录。"},
    ).status_code == 200
    before = datetime.now(timezone.utc)
    resolved = client.post(
        f"/api/admin/complaints/{complaint_id}/resolve",
        headers=admin,
        json={
            "decision": "institution_fault_refund",
            "decision_note": "认定机构承担责任并全额退款。",
        },
    )
    after = datetime.now(timezone.utc)
    assert resolved.status_code == 200, resolved.get_json()
    with app.app_context():
        case = RefundCase.query.filter_by(complaint_id=complaint_id).one()
        due = case.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        assert case.status == "institution_action_required"
        assert case.payment_item.fund_status == "refund_required"
        assert before + timedelta(hours=72) <= due <= after + timedelta(hours=72)


def test_historical_fulfilled_appointment_is_backfilled_at_upgrade_time(app, client):
    payer, created = create_order(app, client, days=9)
    order_id = created["payment_order"]["id"]
    appointment_id = created["item"]["id"]
    assert client.post(f"/api/payment-orders/{order_id}/pay", headers=payer).status_code == 200
    with app.app_context():
        FinanceLedgerEntry.query.delete(synchronize_session=False)
        FinanceTransaction.query.delete(synchronize_session=False)
        item = PaymentOrderItem.query.filter_by(appointment_id=appointment_id).one()
        order = item.order
        db.session.delete(order)
        appointment = db.session.get(Appointment, appointment_id)
        appointment.status = "fulfilled"
        appointment.fulfilled_at = datetime.now(timezone.utc) - timedelta(days=30)
        db.session.commit()
        migrated_at = datetime.now(timezone.utc)
        assert backfill_historical_settlements(now=migrated_at) >= 1
        db.session.commit()
        historical = PaymentOrderItem.query.filter_by(appointment_id=appointment_id).one()
        assert historical.order.source == "historical"
        assert historical.fund_status == "settled"
        assert historical.settled_at.replace(tzinfo=timezone.utc) == migrated_at
