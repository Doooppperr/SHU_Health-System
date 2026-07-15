from __future__ import annotations

from sqlalchemy import inspect

from app.extensions import db


CURRENT_SCHEMA_VERSION = 4


class SchemaUpgradeRequired(RuntimeError):
    """Raised when a non-empty SQLite database uses an older schema."""


def _sqlite_user_version(connection) -> int:
    return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def _schema_shape_issues(connection) -> list[str]:
    inspector = inspect(connection)
    actual_tables = {
        name for name in inspector.get_table_names() if not name.startswith("sqlite_")
    }
    expected_tables = set(db.metadata.tables)
    issues = [f"missing table {name}" for name in sorted(expected_tables - actual_tables)]

    table_sql = {
        row[0]: row[1] or ""
        for row in connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for table_name in sorted(expected_tables & actual_tables):
        expected_columns = set(db.metadata.tables[table_name].columns.keys())
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        issues.extend(
            f"missing column {table_name}.{name}"
            for name in sorted(expected_columns - actual_columns)
        )
        normalized_sql = table_sql.get(table_name, "").lower()
        for constraint in db.metadata.tables[table_name].constraints:
            if constraint.name and constraint.name.lower() not in normalized_sql:
                issues.append(f"missing constraint {constraint.name}")
    return issues


def initialize_or_validate_schema() -> None:
    """Create a fresh v4 schema or reject a non-empty legacy database.

    ``db.create_all`` cannot add columns or replace SQLite CHECK constraints.
    Rejecting legacy files before creating missing tables prevents a partially
    upgraded database that combines old tables with new ones.
    """

    with db.engine.begin() as connection:
        if connection.dialect.name != "sqlite":
            db.metadata.create_all(bind=connection)
            return

        tables = {
            name
            for name in inspect(connection).get_table_names()
            if not name.startswith("sqlite_")
        }
        version = _sqlite_user_version(connection)

        if not tables:
            db.metadata.create_all(bind=connection)
            connection.exec_driver_sql(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
            return

        if version != CURRENT_SCHEMA_VERSION:
            raise SchemaUpgradeRequired(
                "SQLite schema upgrade required: "
                f"database version is {version}, expected {CURRENT_SCHEMA_VERSION}. "
                "Stop the backend and run backend/scripts/upgrade_local_database.py."
            )

        issues = _schema_shape_issues(connection)
        if issues:
            preview = "; ".join(issues[:5])
            if len(issues) > 5:
                preview += f"; and {len(issues) - 5} more"
            raise SchemaUpgradeRequired(
                "SQLite schema is marked as v4 but its structure is incomplete: "
                f"{preview}. Stop the backend and run "
                "backend/scripts/upgrade_local_database.py --check-only."
            )
