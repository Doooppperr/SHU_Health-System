from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class PaymentOrder(db.Model):
    __tablename__ = "payment_orders"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('pending','paid','partially_refunded','refunded','expired')",
            name="ck_payment_orders_status",
        ),
        db.CheckConstraint("amount >= 0", name="ck_payment_orders_amount"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_no = db.Column(db.String(40), nullable=False, unique=True, index=True)
    booking_group_id = db.Column(
        db.Integer,
        db.ForeignKey("booking_groups.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    payer_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    currency = db.Column(db.String(3), nullable=False, default="CNY", server_default="CNY")
    status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    source = db.Column(db.String(30), nullable=False, default="online", server_default="online")
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expired_at = db.Column(db.DateTime(timezone=True), nullable=True)
    refunded_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    booking_group = db.relationship("BookingGroup")
    payer = db.relationship("User")
    items = db.relationship(
        "PaymentOrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="PaymentOrderItem.id.asc()",
    )

    def to_dict(self, *, include_items=True):
        payload = {
            "id": self.id,
            "order_no": self.order_no,
            "booking_group_id": self.booking_group_id,
            "amount": float(self.amount),
            "currency": self.currency,
            "status": self.status,
            "status_label": {
                "pending": "待付款",
                "paid": "已付款",
                "partially_refunded": "部分已退款",
                "refunded": "已退款",
                "expired": "付款超时",
            }.get(self.status, "处理中"),
            "source": self.source,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "refunded_at": self.refunded_at.isoformat() if self.refunded_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_items:
            payload["items"] = [item.to_dict() for item in self.items]
        return payload


class PaymentOrderItem(db.Model):
    __tablename__ = "payment_order_items"
    __table_args__ = (
        db.CheckConstraint(
            "fund_status in ('pending','held','scheduled','settled','refund_required','refunded')",
            name="ck_payment_order_items_fund_status",
        ),
        db.CheckConstraint("gross_amount >= 0", name="ck_payment_order_items_gross"),
        db.CheckConstraint("fee_amount >= 0", name="ck_payment_order_items_fee"),
        db.CheckConstraint("net_amount >= 0", name="ck_payment_order_items_net"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("payment_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    appointment_id = db.Column(
        db.Integer,
        db.ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    institution_id = db.Column(
        db.Integer,
        db.ForeignKey("institutions.id"),
        nullable=False,
        index=True,
    )
    gross_amount = db.Column(db.Numeric(12, 2), nullable=False)
    fee_rate = db.Column(db.Numeric(7, 6), nullable=False, default=Decimal("0.025000"))
    fee_amount = db.Column(db.Numeric(12, 2), nullable=False)
    net_amount = db.Column(db.Numeric(12, 2), nullable=False)
    fund_status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    settlement_due_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    settled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    refund_required_at = db.Column(db.DateTime(timezone=True), nullable=True)
    refund_due_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    refunded_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    order = db.relationship("PaymentOrder", back_populates="items")
    appointment = db.relationship("Appointment")
    institution = db.relationship("Institution")
    refund_case = db.relationship("RefundCase", back_populates="payment_item", uselist=False)

    def to_dict(self):
        appointment = self.appointment
        report = appointment.report if appointment else None
        return {
            "id": self.id,
            "appointment_id": self.appointment_id,
            "institution_id": self.institution_id,
            "subject_name": appointment.user_name_snapshot if appointment else None,
            "package_name": appointment.package_name_snapshot if appointment else None,
            "gross_amount": float(self.gross_amount),
            "fee_rate": float(self.fee_rate),
            "fee_amount": float(self.fee_amount),
            "net_amount": float(self.net_amount),
            "fund_status": self.fund_status,
            "fund_status_label": {
                "pending": "待付款",
                "held": "平台托管",
                "scheduled": "待结算",
                "settled": "已到账",
                "refund_required": "待机构退款",
                "refunded": "已退款",
            }.get(self.fund_status, "处理中"),
            "report_published_at": report.published_at.isoformat() if report and report.published_at else None,
            "settlement_due_at": self.settlement_due_at.isoformat() if self.settlement_due_at else None,
            "settled_at": self.settled_at.isoformat() if self.settled_at else None,
            "refund_due_at": self.refund_due_at.isoformat() if self.refund_due_at else None,
            "refunded_at": self.refunded_at.isoformat() if self.refunded_at else None,
        }


class FinanceTransaction(db.Model):
    __tablename__ = "finance_transactions"

    id = db.Column(db.Integer, primary_key=True)
    transaction_no = db.Column(db.String(48), nullable=False, unique=True, index=True)
    transaction_type = db.Column(db.String(40), nullable=False, index=True)
    idempotency_key = db.Column(db.String(180), nullable=False, unique=True, index=True)
    payment_item_id = db.Column(
        db.Integer,
        db.ForeignKey("payment_order_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey("appointment_complaints.id", ondelete="SET NULL"), nullable=True)
    gross_amount = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    fee_amount = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    net_amount = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)

    entries = db.relationship("FinanceLedgerEntry", back_populates="transaction", cascade="all, delete-orphan")


class FinanceLedgerEntry(db.Model):
    __tablename__ = "finance_ledger_entries"
    __table_args__ = (
        db.CheckConstraint(
            "account_type in ('platform_custody','platform_fee','institution_available')",
            name="ck_finance_ledger_entries_account",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(
        db.Integer,
        db.ForeignKey("finance_transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_type = db.Column(db.String(40), nullable=False, index=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), nullable=True, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)

    transaction = db.relationship("FinanceTransaction", back_populates="entries")


class RefundCase(db.Model):
    __tablename__ = "refund_cases"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('requested','institution_approved','platform_awarded','institution_action_required','refunded','denied')",
            name="ck_refund_cases_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(
        db.Integer,
        db.ForeignKey("appointment_complaints.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    payment_item_id = db.Column(
        db.Integer,
        db.ForeignKey("payment_order_items.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status = db.Column(db.String(40), nullable=False, default="requested", index=True)
    decision = db.Column(db.String(40), nullable=True)
    decision_note = db.Column(db.Text, nullable=True)
    decided_at = db.Column(db.DateTime(timezone=True), nullable=True)
    due_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    refunded_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    complaint = db.relationship("AppointmentComplaint", back_populates="refund_case")
    payment_item = db.relationship("PaymentOrderItem", back_populates="refund_case")

    def to_dict(self):
        item = self.payment_item
        fund_status = item.fund_status if item else None
        return {
            "id": self.id,
            "status": self.status,
            "decision": self.decision,
            "decision_note": self.decision_note,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "refunded_at": self.refunded_at.isoformat() if self.refunded_at else None,
            "amount": float(item.gross_amount) if item else None,
            "fund_status": fund_status,
            "fund_location": (
                "已原路退回" if fund_status == "refunded"
                else "机构账户" if fund_status in {"settled", "refund_required"}
                else "平台托管" if fund_status in {"held", "scheduled"}
                else "待付款"
            ),
        }
