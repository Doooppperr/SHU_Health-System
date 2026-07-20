from __future__ import annotations

from sqlalchemy import inspect

from app.extensions import db


CURRENT_SCHEMA_VERSION = 7


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

    table_sql = {}
    if connection.dialect.name == "sqlite":
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
        if connection.dialect.name == "sqlite":
            actual_constraint_names = {
                constraint.name.lower()
                for constraint in db.metadata.tables[table_name].constraints
                if constraint.name
                and constraint.name.lower() in table_sql.get(table_name, "").lower()
            }
        else:
            reflected = []
            for getter_name in (
                "get_check_constraints",
                "get_unique_constraints",
                "get_foreign_keys",
            ):
                reflected.extend(getattr(inspector, getter_name)(table_name) or [])
            primary_key = inspector.get_pk_constraint(table_name) or {}
            reflected.append(primary_key)
            actual_constraint_names = {
                str(item.get("name") or "").lower()
                for item in reflected
                if item.get("name")
            }
        for constraint in db.metadata.tables[table_name].constraints:
            if constraint.name and constraint.name.lower() not in actual_constraint_names:
                issues.append(f"missing constraint {constraint.name}")
    if "users" in actual_tables:
        for constraint in inspector.get_unique_constraints("users"):
            if tuple(constraint.get("column_names") or ()) == ("email",):
                issues.append("obsolete unique constraint on users.email")
    return issues


def initialize_or_validate_schema() -> None:
    """Create a fresh v7 schema or reject a non-empty legacy database.

    ``db.create_all`` cannot add columns or replace SQLite CHECK constraints.
    Rejecting legacy files before creating missing tables prevents a partially
    upgraded database that combines old tables with new ones.
    """

    with db.engine.begin() as connection:
        if connection.dialect.name != "sqlite":
            tables = {name for name in inspect(connection).get_table_names() if not name.startswith("alembic_")}
            if not tables:
                db.metadata.create_all(bind=connection)
                return
            issues = _schema_shape_issues(connection)
            if issues:
                preview = "; ".join(issues[:5])
                raise SchemaUpgradeRequired(
                    f"openGauss/GaussDB schema upgrade required: {preview}. "
                    "Run the schema v7 Alembic migration before starting the application."
                )
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
                "SQLite schema is marked as v7 but its structure is incomplete: "
                f"{preview}. Stop the backend and run "
                "backend/scripts/upgrade_local_database.py --check-only."
            )
