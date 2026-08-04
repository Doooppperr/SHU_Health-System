"""Read-only validator for the complete schema-v13 acceptance snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
for path in (SCRIPT_DIR, BACKEND_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from validate_v10_demo import create_read_only_validation_app, main as validate_v10_demo  # noqa: E402
from validate_v12_demo import validate_v12_contract  # noqa: E402
from app.config import config_by_name  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    Appointment,
    FinanceLedgerEntry,
    Institution,
    PaymentOrder,
    PaymentOrderItem,
    RefundCase,
)


REQUIRED_TABLES = {
    "payment_orders",
    "payment_order_items",
    "finance_transactions",
    "finance_ledger_entries",
    "refund_cases",
}
REQUIRED_ORDER_STATUSES = {"pending", "paid", "refunded", "expired"}
REQUIRED_FUND_STATUSES = {
    "pending", "held", "scheduled", "settled", "refund_required", "refunded",
}


def _validate_schema() -> dict:
    connection = db.session.connection()
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise RuntimeError(f"schema-v13 finance tables are missing: {missing}")
    user_version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
    revision = connection.exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).scalar_one()
    integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()
    foreign_keys = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if user_version != 13 or revision != "20260804_schema_v13":
        raise RuntimeError(
            f"schema-v13 marker mismatch: user_version={user_version}, revision={revision}"
        )
    if integrity != "ok" or foreign_keys:
        raise RuntimeError(
            f"SQLite storage check failed: integrity={integrity}, foreign_keys={len(foreign_keys)}"
        )
    columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(institutions)")
    }
    required_columns = {"operations_suspended_at", "operations_suspension_reason"}
    if not required_columns <= columns:
        raise RuntimeError("institution operation-suspension columns are missing")
    return {
        "schema_version": user_version,
        "alembic_revision": revision,
        "sqlite_integrity": integrity,
        "sqlite_foreign_key_errors": 0,
    }


def _validate_finance() -> dict:
    order_counts = Counter(row.status for row in PaymentOrder.query.all())
    fund_counts = Counter(row.fund_status for row in PaymentOrderItem.query.all())
    if not REQUIRED_ORDER_STATUSES <= set(order_counts):
        raise RuntimeError(f"payment-order states are incomplete: {dict(order_counts)}")
    if not REQUIRED_FUND_STATUSES <= set(fund_counts):
        raise RuntimeError(f"payment-item states are incomplete: {dict(fund_counts)}")

    cents = Decimal("0.01")
    for item in PaymentOrderItem.query.all():
        gross = Decimal(item.gross_amount).quantize(cents)
        fee = (gross * Decimal("0.025")).quantize(cents, rounding=ROUND_HALF_UP)
        if Decimal(item.fee_amount).quantize(cents) != fee:
            raise RuntimeError(f"payment item {item.id} has an invalid 2.5% fee")
        if Decimal(item.net_amount).quantize(cents) != gross - fee:
            raise RuntimeError(f"payment item {item.id} has an invalid net amount")

    fulfilled_without_finance = Appointment.query.filter_by(status="fulfilled").filter(
        ~Appointment.id.in_(db.session.query(PaymentOrderItem.appointment_id))
    ).count()
    if fulfilled_without_finance:
        raise RuntimeError("fulfilled historical appointments were not fully backfilled")

    refund_counts = Counter(row.status for row in RefundCase.query.all())
    required_refund_states = {"requested", "institution_action_required", "denied"}
    if not required_refund_states <= set(refund_counts):
        raise RuntimeError(f"refund-case states are incomplete: {dict(refund_counts)}")
    overdue = PaymentOrderItem.query.filter(
        PaymentOrderItem.fund_status == "refund_required",
        PaymentOrderItem.refund_due_at < db.func.current_timestamp(),
    ).count()
    suspended = Institution.query.filter(Institution.operations_suspended_at.isnot(None)).count()
    if overdue < 1 or suspended < 1:
        raise RuntimeError("overdue-refund suspension scenario is missing")

    balances = {
        account: Decimal(value or 0).quantize(cents)
        for account, value in db.session.query(
            FinanceLedgerEntry.account_type,
            db.func.sum(FinanceLedgerEntry.amount),
        ).group_by(FinanceLedgerEntry.account_type)
    }
    if any(value < 0 for value in balances.values()):
        raise RuntimeError(f"acceptance ledger has a negative account balance: {balances}")
    return {
        "payment_orders": PaymentOrder.query.count(),
        "payment_order_statuses": dict(order_counts),
        "payment_item_statuses": dict(fund_counts),
        "refund_case_statuses": dict(refund_counts),
        "suspended_institutions": suspended,
        "ledger_balances": {key: f"{value:.2f}" for key, value in balances.items()},
    }


def main(database_path: Path | None = None, upload_dir: Path | None = None) -> int:
    if database_path is not None:
        database_path = database_path.expanduser().resolve()
        config_by_name["development"].SQLALCHEMY_DATABASE_URI = (
            f"sqlite:///{database_path.as_posix()}"
        )
    upload_dir = (upload_dir or BACKEND_DIR / "uploads").expanduser().resolve()
    config_by_name["development"].UPLOAD_DIR = str(upload_dir)
    if validate_v10_demo(database_path, upload_dir) != 0:
        raise RuntimeError("schema-v10 clinical/media baseline validation failed")
    app = create_read_only_validation_app()
    with app.app_context():
        summary = _validate_schema()
        summary.update(validate_v12_contract())
        summary.update(_validate_finance())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate the schema-v13 acceptance database.")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--upload-dir", type=Path, default=BACKEND_DIR / "uploads")
    arguments = parser.parse_args()
    raise SystemExit(main(arguments.database, arguments.upload_dir))
