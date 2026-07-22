"""Apply the additive HealthDoc schema v9 migration without loading Flask.

This entry point is used by the production release helper before the new app
is started, because application startup intentionally rejects older schemas.
"""

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
            inspector = inspect(connection)
            before_counts = {
                name: connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in CORE_TABLES if name in inspector.get_table_names()
            }
            user_columns = {column["name"] for column in inspector.get_columns("users")}
            if "token_version" not in user_columns:
                connection.execute(text('ALTER TABLE "users" ADD COLUMN "token_version" INTEGER NOT NULL DEFAULT 0'))
            for name in ("password_verification_challenges", "comment_replies"):
                db.metadata.tables[name].create(bind=connection, checkfirst=True)
            if "alembic_version" in inspector.get_table_names():
                current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
                if current == "20260720_schema_v8":
                    connection.execute(text("UPDATE alembic_version SET version_num='20260722_schema_v9'"))
            elif connection.dialect.name != "sqlite":
                # 部分早期生产库由完整数据导入建立，没有 Alembic 版本表。
                # 数据结构完成 v9 的幂等补齐后再补写版本落章，便于后续迁移接续。
                connection.execute(
                    text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                )
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES ('20260722_schema_v9')")
                )
        with engine.connect() as connection:
            inspector = inspect(connection)
            missing = {"password_verification_challenges", "comment_replies"} - set(inspector.get_table_names())
            columns = {column["name"] for column in inspector.get_columns("users")}
            if missing or "token_version" not in columns:
                raise RuntimeError(f"schema v9 verification failed; missing tables={sorted(missing)}")
            after_counts = {
                name: connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in before_counts
            }
            if after_counts != before_counts:
                raise RuntimeError(f"core data counts changed during migration: before={before_counts}, after={after_counts}")
            print(f"core_data_counts={after_counts}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    migrate(os.environ.get("DATABASE_URL", ""))
    print("schema_v9_migration=ok")
