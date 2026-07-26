"""Apply the additive HealthDoc schema v10 migration without loading Flask."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.extensions import db  # noqa: E402
import app.models  # noqa: E402,F401


CORE_TABLES = ("users", "institutions", "appointments", "institution_reports", "comments", "packages")


def migrate(database_url: str) -> None:
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            if connection.dialect.name == "sqlite":
                raise RuntimeError("SQLite must be upgraded with scripts/upgrade_local_database.py")
            inspector = inspect(connection)
            before = {
                name: connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in CORE_TABLES
                if name in inspector.get_table_names()
            }
            db.metadata.create_all(bind=connection, checkfirst=True)
            columns = {item["name"] for item in inspect(connection).get_columns("appointments")}
            appointment_columns = {
                "height_cm_snapshot": "NUMERIC(6,2)",
                "weight_kg_snapshot": "NUMERIC(6,2)",
                "bmi_snapshot": "NUMERIC(5,2)",
                "allergy_history_snapshot": "TEXT",
                "medical_history_snapshot": "TEXT",
                "intake_captured_at": "TIMESTAMP WITH TIME ZONE",
                "termination_party": "VARCHAR(20)",
                "termination_reason_code": "VARCHAR(40)",
                "termination_reason_text": "VARCHAR(500)",
            }
            for name, ddl in appointment_columns.items():
                if name not in columns:
                    connection.execute(text(f'ALTER TABLE "appointments" ADD COLUMN "{name}" {ddl}'))
            result_columns = {item["name"] for item in inspect(connection).get_columns("report_indicators")}
            if "result_status" not in result_columns:
                connection.execute(text("ALTER TABLE report_indicators ADD COLUMN result_status VARCHAR(20) NOT NULL DEFAULT 'unknown'"))
            asset_columns = {item["name"] for item in inspect(connection).get_columns("report_assets")}
            if "asset_type_id" not in asset_columns:
                connection.execute(text("ALTER TABLE report_assets ADD COLUMN asset_type_id INTEGER REFERENCES report_asset_types(id)"))
            connection.execute(text("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS ck_appointments_status"))
            connection.execute(text("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS ck_appointments_termination_party"))
            connection.execute(text("UPDATE appointments SET status='no_show', termination_party='subject', termination_reason_code='legacy_no_show' WHERE status='invalidated'"))
            connection.execute(text(
                "ALTER TABLE appointments ADD CONSTRAINT ck_appointments_status "
                "CHECK (status in ('unfulfilled','awaiting_report','fulfilled','cancelled','no_show','institution_cancelled'))"
            ))
            connection.execute(text(
                "ALTER TABLE appointments ADD CONSTRAINT ck_appointments_termination_party "
                "CHECK (termination_party is null or termination_party in ('user','institution','subject'))"
            ))
            connection.execute(text("UPDATE report_indicators SET result_status=CASE WHEN is_abnormal THEN 'abnormal' ELSE 'normal' END WHERE result_status='unknown'"))
            connection.execute(text("ALTER TABLE report_indicators DROP CONSTRAINT IF EXISTS ck_report_indicators_result_status"))
            connection.execute(text(
                "ALTER TABLE report_indicators ADD CONSTRAINT ck_report_indicators_result_status "
                "CHECK (result_status in ('normal','high','low','positive','negative','abnormal','unknown'))"
            ))
            if "alembic_version" in inspect(connection).get_table_names():
                connection.execute(text("UPDATE alembic_version SET version_num='20260726_schema_v10'"))
        with engine.connect() as connection:
            after = {
                name: connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in before
            }
            if before != after:
                raise RuntimeError(f"core data counts changed: before={before}, after={after}")
            required = {"user_notifications", "report_asset_types", "package_version_asset_requirements", "indicator_reference_rules"}
            final_inspector = inspect(connection)
            missing = required - set(final_inspector.get_table_names())
            if missing:
                raise RuntimeError(f"schema v10 verification failed: missing={sorted(missing)}")
            required_columns = {
                "appointments": set(appointment_columns),
                "report_indicators": {"result_status"},
                "report_assets": {"asset_type_id"},
            }
            missing_columns = {
                table: sorted(names - {column["name"] for column in final_inspector.get_columns(table)})
                for table, names in required_columns.items()
            }
            missing_columns = {table: names for table, names in missing_columns.items() if names}
            if missing_columns:
                raise RuntimeError(f"schema v10 verification failed: missing_columns={missing_columns}")
            expected_unique_constraints = {
                "user_notifications": {"uq_user_notifications_idempotency"},
                "report_asset_types": {"uq_report_asset_types_code"},
                "package_version_asset_requirements": {"uq_package_asset_requirement"},
            }
            for table, expected_names in expected_unique_constraints.items():
                present_names = {
                    constraint.get("name")
                    for constraint in final_inspector.get_unique_constraints(table)
                }
                if not expected_names.issubset(present_names):
                    raise RuntimeError(
                        f"schema v10 verification failed: {table} unique constraints "
                        f"expected={sorted(expected_names)}, actual={sorted(name for name in present_names if name)}"
                    )
            print(f"core_data_counts={after}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    migrate(os.environ.get("DATABASE_URL", ""))
    print("schema_v10_migration=ok")
