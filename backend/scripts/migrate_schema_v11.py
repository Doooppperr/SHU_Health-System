"""Apply the additive HealthDoc schema v11 migration without losing business data."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, inspect, text


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.extensions import db  # noqa: E402
import app.models  # noqa: E402,F401
from scripts.migrate_schema_v10 import CORE_TABLES, migrate as migrate_v10  # noqa: E402


REQUIRED_V11_TABLES = {
    "agent_threads",
    "agent_runs",
    "agent_tool_events",
    "agent_pending_actions",
    "agent_action_executions",
    "support_handoffs",
    "oauth_clients",
    "oauth_authorization_codes",
    "oauth_access_tokens",
    "oauth_refresh_tokens",
}


def migrate(database_url: str) -> None:
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    migrate_v10(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            before = {
                name: connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in CORE_TABLES
                if name in inspect(connection).get_table_names()
            }
            db.metadata.create_all(bind=connection, checkfirst=True)
            if "alembic_version" in inspect(connection).get_table_names():
                connection.execute(
                    text(
                        "UPDATE alembic_version "
                        "SET version_num='20260729_schema_v11'"
                    )
                )
        with engine.connect() as connection:
            inspector = inspect(connection)
            missing = REQUIRED_V11_TABLES - set(inspector.get_table_names())
            if missing:
                raise RuntimeError(
                    f"schema v11 verification failed: missing={sorted(missing)}"
                )
            after = {
                name: connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in before
            }
            if before != after:
                raise RuntimeError(
                    f"core data counts changed: before={before}, after={after}"
                )
            print(f"core_data_counts={after}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    migrate(os.environ.get("DATABASE_URL", ""))
    print("schema_v11_migration=ok")
