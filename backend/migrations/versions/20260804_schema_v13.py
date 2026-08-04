"""HealthDoc schema v13: payment, settlement and refund ledger.

Revision ID: 20260804_schema_v13
Revises: 20260730_schema_v12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260804_schema_v13"
down_revision = "20260730_schema_v12"
branch_labels = None
depends_on = None


APPOINTMENT_STATUS_CHECK = (
    "status in ('pending_payment','payment_expired','unfulfilled',"
    "'awaiting_report','fulfilled','cancelled','no_show','institution_cancelled')"
)


def _columns(table_name):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade():
    if "operations_suspended_at" not in _columns("institutions"):
        op.add_column("institutions", sa.Column("operations_suspended_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index("ix_institutions_operations_suspended_at", "institutions", ["operations_suspended_at"])
    if "operations_suspension_reason" not in _columns("institutions"):
        op.add_column("institutions", sa.Column("operations_suspension_reason", sa.String(length=500), nullable=True))

    with op.batch_alter_table("appointments") as batch:
        batch.drop_constraint("ck_appointments_status", type_="check")
        batch.create_check_constraint("ck_appointments_status", APPOINTMENT_STATUS_CHECK)

    from app.models import (
        FinanceLedgerEntry,
        FinanceTransaction,
        PaymentOrder,
        PaymentOrderItem,
        RefundCase,
    )

    bind = op.get_bind()
    for table in (
        PaymentOrder.__table__,
        PaymentOrderItem.__table__,
        FinanceTransaction.__table__,
        FinanceLedgerEntry.__table__,
        RefundCase.__table__,
    ):
        table.create(bind=bind, checkfirst=True)


def downgrade():
    for table_name in (
        "refund_cases",
        "finance_ledger_entries",
        "finance_transactions",
        "payment_order_items",
        "payment_orders",
    ):
        op.drop_table(table_name)
    with op.batch_alter_table("appointments") as batch:
        batch.drop_constraint("ck_appointments_status", type_="check")
        batch.create_check_constraint(
            "ck_appointments_status",
            "status in ('unfulfilled','awaiting_report','fulfilled','cancelled','no_show','institution_cancelled')",
        )
    op.drop_index("ix_institutions_operations_suspended_at", table_name="institutions")
    op.drop_column("institutions", "operations_suspension_reason")
    op.drop_column("institutions", "operations_suspended_at")
