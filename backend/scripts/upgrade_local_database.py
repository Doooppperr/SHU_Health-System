"""Upgrade the local SQLite database to HealthDoc schema v9.

The v6-to-v7 path preserves all current business data while adding health
domains, package versions, booking groups, waitlists and private assets. Older
supported snapshots are copied by common
columns into the current schema; much older unsupported schemas
retain only the current system administrator identity before rebuilding.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = BACKEND_DIR / "instance" / "health_system.db"
SCHEMA_VERSION = 9
sys.path.insert(0, str(BACKEND_DIR))

from app import models as _models  # noqa: E402,F401
from app.extensions import db  # noqa: E402


@dataclass(frozen=True)
class SchemaReport:
    version: int
    missing_tables: tuple[str, ...]
    missing_columns: tuple[str, ...]
    missing_constraints: tuple[str, ...]

    @property
    def is_current(self):
        return self.version == SCHEMA_VERSION and not self.missing_tables and not self.missing_columns and not self.missing_constraints


def parse_args():
    parser = argparse.ArgumentParser(description="Upgrade the local SQLite database to schema v9.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def table_names(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def inspect_schema(connection):
    tables = table_names(connection)
    expected = set(db.metadata.tables)
    missing_columns = []
    for name in sorted(expected & tables):
        actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')}
        missing_columns.extend(f"{name}.{column.name}" for column in db.metadata.tables[name].columns if column.name not in actual)
    ddl = "\n".join((row[0] or "") for row in connection.execute("SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND sql IS NOT NULL"))
    named = {constraint.name for table in db.metadata.tables.values() for constraint in table.constraints if constraint.name}
    incompatible_constraints = []
    if "users" in tables:
        for index_row in connection.execute('PRAGMA index_list("users")'):
            if not index_row[2]:
                continue
            columns = tuple(
                row[2] for row in connection.execute(
                    f'PRAGMA index_info("{index_row[1]}")'
                )
            )
            if columns == ("email",):
                incompatible_constraints.append("users.email_unique_must_be_removed")
    return SchemaReport(
        int(connection.execute("PRAGMA user_version").fetchone()[0]),
        tuple(sorted(expected - tables)), tuple(missing_columns),
        tuple(sorted([*(name for name in named if name not in ddl), *incompatible_constraints])),
    )


def validate(connection):
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("SQLite integrity_check failed")
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"SQLite foreign_key_check found {len(violations)} violation(s)")
    report = inspect_schema(connection)
    if not report.is_current:
        raise RuntimeError(f"schema v9 validation failed: {report}")


def read_admin(connection):
    if "users" not in table_names(connection):
        return None
    columns = {row[1] for row in connection.execute('PRAGMA table_info("users")')}
    required = {"id", "username", "password_hash"}
    if not required <= columns:
        return None
    optional = [name for name in ("email", "phone", "created_at", "is_active") if name in columns]
    selected = ["id", "username", "password_hash", *optional]
    role_clause = "role = 'admin'" if "role" in columns else "username = 'admin'"
    row = connection.execute(f"SELECT {', '.join(selected)} FROM users WHERE {role_clause} ORDER BY CASE WHEN username='admin' THEN 0 ELSE 1 END, id LIMIT 1").fetchone()
    return dict(zip(selected, row)) if row else None


def backup_path(database):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return database.with_name(f"{database.stem}.before-schema-v9-{stamp}-{uuid.uuid4().hex[:6]}.db")


def prepare_v8_source(database_path):
    """Add deterministic organization rows to a temporary legacy snapshot."""
    prepared = database_path.with_name(f".{database_path.stem}.v8-source-{uuid.uuid4().hex}.db")
    shutil.copy2(database_path, prepared)
    connection = sqlite3.connect(prepared)
    try:
        tables = table_names(connection)
        if "institutions" in tables and "organizations" not in tables:
            connection.execute("CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL UNIQUE, description TEXT, service_features JSON NOT NULL DEFAULT '[]', is_active BOOLEAN NOT NULL DEFAULT 1, created_at DATETIME NOT NULL)")
            columns = {row[1] for row in connection.execute('PRAGMA table_info("institutions")')}
            if "organization_id" not in columns:
                connection.execute("ALTER TABLE institutions ADD COLUMN organization_id INTEGER")
            rows = connection.execute("SELECT name, MIN(id) FROM institutions GROUP BY name ORDER BY MIN(id)").fetchall()
            for index, (name, _first_id) in enumerate(rows, start=1):
                connection.execute("INSERT INTO organizations (id,name,description,service_features,is_active,created_at) VALUES (?,?,?,?,1,?)", (index, name, f"{name}旗下体检服务机构。", "[]", datetime.now().isoformat()))
                connection.execute("UPDATE institutions SET organization_id=? WHERE name=?", (index, name))
        connection.commit()
    except Exception:
        connection.close(); prepared.unlink(missing_ok=True); raise
    finally:
        connection.close()
    return prepared


def rebuild_database(database_path):
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database_path}")
    with closing(sqlite3.connect(database_path)) as source:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("source SQLite integrity_check failed")
        report = inspect_schema(source)
        if report.is_current:
            validate(source)
            return None
        admin = read_admin(source)
        available_tables = table_names(source)

    if report.version in {4, 5, 6, 7, 8}:
        from scripts.migrate_sqlite_to_gaussdb import migrate

        temporary = database_path.with_name(f".{database_path.stem}.v9-{uuid.uuid4().hex}.db")
        prepared = prepare_v8_source(database_path)
        backup = backup_path(database_path)
        try:
            migrate(
                prepared,
                f"sqlite:///{temporary.as_posix()}",
                replace=True,
                allow_legacy_source=True,
            )
            with closing(sqlite3.connect(temporary)) as target:
                target.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                target.commit()
                validate(target)
            shutil.copy2(database_path, backup)
            os.replace(temporary, database_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)
            raise
        finally:
            prepared.unlink(missing_ok=True)
        return backup

    temporary = database_path.with_name(f".{database_path.stem}.v9-{uuid.uuid4().hex}.db")
    backup = backup_path(database_path)
    engine = create_engine(f"sqlite:///{temporary.as_posix()}")
    try:
        db.metadata.create_all(engine)
    finally:
        engine.dispose()
    target = sqlite3.connect(temporary)
    try:
        target.execute("PRAGMA foreign_keys=ON")
        if admin:
            target.execute(
                "INSERT INTO users (id, username, password_hash, email, phone, role, managed_institution_id, health_id, is_active, created_at) VALUES (?, ?, ?, ?, ?, 'admin', NULL, NULL, ?, ?)",
                (admin["id"], admin["username"], admin["password_hash"], admin.get("email"), admin.get("phone"), admin.get("is_active", 1), admin.get("created_at") or datetime.now().isoformat()),
            )
        target.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        target.commit()
        validate(target)
    except Exception:
        target.close(); temporary.unlink(missing_ok=True); raise
    finally:
        if temporary.exists():
            try: target.close()
            except Exception: pass
    shutil.copy2(database_path, backup)
    try:
        os.replace(temporary, database_path)
    except Exception:
        temporary.unlink(missing_ok=True); backup.unlink(missing_ok=True); raise
    return backup


def print_report(database, report):
    print(f"database={database}")
    print(f"user_version={report.version}")
    print(f"expected_user_version={SCHEMA_VERSION}")
    print(f"schema_current={'yes' if report.is_current else 'no'}")
    for label, values in (("table", report.missing_tables), ("column", report.missing_columns), ("constraint", report.missing_constraints)):
        print(f"missing_{label}s={len(values)}")
        for value in values: print(f"{label}:{value}")


def main():
    args = parse_args(); database = args.database.resolve()
    if args.check_only:
        with closing(sqlite3.connect(database)) as connection: print_report(database, inspect_schema(connection))
        return
    backup = rebuild_database(database)
    print(f"database={database}")
    print("schema_upgrade=already-current" if backup is None else f"backup={backup}\nschema_upgrade=ok")


if __name__ == "__main__":
    main()
