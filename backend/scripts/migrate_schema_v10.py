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
            # An old boolean ``is_abnormal = false`` does not prove a result is
            # normal. Keep such rows unknown until a real institution range,
            # demographic rule or catalog range can classify them.
            connection.execute(text(
                "UPDATE report_indicators SET result_status='abnormal' "
                "WHERE result_status='unknown' AND is_abnormal"
            ))
            connection.execute(text(
                "UPDATE report_indicators SET result_status='unknown', is_abnormal=FALSE "
                "WHERE indicator_dict_id IN "
                "(SELECT id FROM indicator_dicts WHERE code IN ('HEIGHT','WEIGHT','HIP'))"
            ))
            connection.execute(text(
                "UPDATE report_indicators SET result_status='unknown', is_abnormal=FALSE "
                "WHERE result_status='normal' "
                "AND COALESCE(TRIM(reference_text),'')='' "
                "AND COALESCE(LOWER(TRIM(abnormal_flag)),'') IN ('','normal','正常') "
                "AND indicator_dict_id IN ("
                "SELECT indicator.id FROM indicator_dicts indicator "
                "WHERE indicator.reference_low IS NULL AND indicator.reference_high IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM indicator_reference_rules rule "
                "WHERE rule.indicator_dict_id=indicator.id "
                "AND (rule.reference_low IS NOT NULL OR rule.reference_high IS NOT NULL))"
                ")"
            ))
            connection.execute(text(
                "UPDATE report_indicators "
                "SET value=CASE "
                "WHEN indicator_dict_id=(SELECT id FROM indicator_dicts WHERE code='HEIGHT') THEN '172' "
                "WHEN indicator_dict_id=(SELECT id FROM indicator_dicts WHERE code='WEIGHT') THEN '68' "
                "ELSE value END, "
                "original_value=CASE "
                "WHEN indicator_dict_id=(SELECT id FROM indicator_dicts WHERE code='HEIGHT') THEN '172' "
                "WHEN indicator_dict_id=(SELECT id FROM indicator_dicts WHERE code='WEIGHT') THEN '68' "
                "ELSE original_value END "
                "WHERE method_snapshot='v10 合成体检演示' "
                "AND indicator_dict_id IN "
                "(SELECT id FROM indicator_dicts WHERE code IN ('HEIGHT','WEIGHT'))"
            ))
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
                "user_notifications": {"uq_user_notifications_user_key"},
                "report_asset_types": {"uq_report_asset_types_code"},
                "package_version_asset_requirements": {"uq_package_version_asset_requirement"},
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
