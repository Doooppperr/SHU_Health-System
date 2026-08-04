from __future__ import annotations

from sqlalchemy import Date, DateTime, ForeignKeyConstraint, UniqueConstraint, inspect

from app.extensions import db


CURRENT_SCHEMA_VERSION = 13


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
        expected_table = db.metadata.tables[table_name]
        reflected_columns = inspector.get_columns(table_name)
        actual_by_name = {
            column["name"]: column for column in reflected_columns
        }
        expected_columns = set(expected_table.columns.keys())
        actual_columns = set(actual_by_name)
        issues.extend(
            f"missing column {table_name}.{name}"
            for name in sorted(expected_columns - actual_columns)
        )
        for column_name in sorted(expected_columns & actual_columns):
            expected_column = expected_table.columns[column_name]
            actual_column = actual_by_name[column_name]
            if (
                not expected_column.primary_key
                and "nullable" in actual_column
                and bool(actual_column["nullable"])
                != bool(expected_column.nullable)
            ):
                issues.append(
                    f"nullability mismatch {table_name}.{column_name}"
                )
            actual_type = actual_column.get("type")
            expected_affinity = getattr(
                expected_column.type,
                "_type_affinity",
                type(expected_column.type),
            )
            actual_affinity = getattr(
                actual_type,
                "_type_affinity",
                type(actual_type) if actual_type is not None else None,
            )
            type_matches = actual_affinity is expected_affinity
            if (
                not type_matches
                and connection.dialect.name == "opengauss"
                and isinstance(expected_column.type, Date)
                and not isinstance(expected_column.type, DateTime)
                and isinstance(actual_type, DateTime)
            ):
                # In the server's openGauss compatibility mode, DATE is a
                # physical alias of TIMESTAMP WITHOUT TIME ZONE and reflects
                # back as DateTime even when DDL explicitly requests DATE.
                type_matches = True
            if actual_type is not None and not type_matches:
                issues.append(f"type mismatch {table_name}.{column_name}")

        reflected_uniques = inspector.get_unique_constraints(table_name) or []
        reflected_indexes = (
            inspector.get_indexes(table_name) or []
            if hasattr(inspector, "get_indexes")
            else []
        )
        actual_unique_columns = {
            tuple(item.get("column_names") or ())
            for item in [*reflected_uniques, *reflected_indexes]
            if item.get("unique") is not False
            and item.get("column_names")
        }
        for constraint in expected_table.constraints:
            if isinstance(constraint, UniqueConstraint) and not constraint.name:
                columns = tuple(column.name for column in constraint.columns)
                if columns not in actual_unique_columns:
                    issues.append(
                        "missing unique constraint "
                        f"{table_name}({','.join(columns)})"
                    )

        reflected_foreign_keys = inspector.get_foreign_keys(table_name) or []
        actual_foreign_keys = {
            (
                tuple(item.get("constrained_columns") or ()),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or ()),
            )
            for item in reflected_foreign_keys
        }
        for constraint in expected_table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            expected_fk = (
                tuple(column.name for column in constraint.columns),
                next(iter(constraint.elements)).column.table.name,
                tuple(element.column.name for element in constraint.elements),
            )
            if expected_fk not in actual_foreign_keys:
                issues.append(
                    "missing foreign key "
                    f"{table_name}({','.join(expected_fk[0])})"
                )

        expected_primary_key = tuple(
            column.name for column in expected_table.primary_key.columns
        )
        reflected_primary_key = inspector.get_pk_constraint(table_name) or {}
        actual_primary_key = tuple(
            reflected_primary_key.get("constrained_columns") or ()
        )
        if expected_primary_key != actual_primary_key:
            issues.append(f"primary key mismatch {table_name}")

        actual_index_names = {
            str(item.get("name") or "").lower()
            for item in reflected_indexes
            if item.get("name")
        }
        for index in expected_table.indexes:
            if index.name and index.name.lower() not in actual_index_names:
                issues.append(f"missing index {index.name}")
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
            ):
                reflected.extend(getattr(inspector, getter_name)(table_name) or [])
            reflected.extend(reflected_uniques)
            reflected.extend(reflected_foreign_keys)
            reflected.append(reflected_primary_key)
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
    """Create a fresh v13 schema or reject a non-empty legacy database.

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
                    "Run the schema v13 Alembic migration before starting the application."
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
                "SQLite schema is marked as v13 but its structure is incomplete: "
                f"{preview}. Stop the backend and run "
                "backend/scripts/upgrade_local_database.py --check-only."
            )
