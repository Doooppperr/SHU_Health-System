"""Apply the HealthDoc schema v13 migration and backfill fulfilled visits."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, inspect, text


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

REVISION = "20260804_schema_v13"
V12_REVISION = "20260730_schema_v12"


def migrate(database_url: str) -> None:
    if not database_url or os.environ.get("DATABASE_URL") != database_url:
        raise RuntimeError("DATABASE_URL must be explicitly configured")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            if not tables:
                raise RuntimeError("refusing to migrate an empty database")
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if revision not in {V12_REVISION, REVISION}:
                raise RuntimeError(f"schema v13 migration requires v12 or v13, found {revision}")
    finally:
        engine.dispose()

    previous = os.environ.get("HEALTHDOC_SCHEMA_MIGRATION")
    os.environ["HEALTHDOC_SCHEMA_MIGRATION"] = "1"
    try:
        from app import create_app
        from app.extensions import db
        from app.services.finance import backfill_historical_settlements
        from flask_migrate import upgrade

        app = create_app("development")
        with app.app_context():
            upgrade(directory=str(BACKEND_DIR / "migrations"), revision=REVISION)
            created = backfill_historical_settlements()
            db.session.commit()
            if db.engine.dialect.name == "sqlite":
                with db.engine.begin() as connection:
                    connection.exec_driver_sql("PRAGMA user_version=13")
            print(f"historical_settlements_created={created}")
    finally:
        if previous is None:
            os.environ.pop("HEALTHDOC_SCHEMA_MIGRATION", None)
        else:
            os.environ["HEALTHDOC_SCHEMA_MIGRATION"] = previous

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            required = {
                "payment_orders", "payment_order_items", "finance_transactions",
                "finance_ledger_entries", "refund_cases",
            }
            missing = required - set(inspect(connection).get_table_names())
            if revision != REVISION or missing:
                raise RuntimeError(f"schema v13 verification failed: revision={revision}, missing={sorted(missing)}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    migrate(os.environ.get("DATABASE_URL", ""))
    print("schema_v13_migration=ok")
