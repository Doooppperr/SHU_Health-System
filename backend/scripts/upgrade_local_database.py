"""Deterministically rebuild the local SQLite database as HealthDoc schema v4.

Version 4 intentionally does not migrate legacy business data.  It preserves
only the current system administrator's primary key and password hash, creates
the new schema in a neighbouring file, verifies it, backs up the old file, and
atomically replaces it.  Normal application startup then creates v4 demo data.
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
SCHEMA_VERSION = 4
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
    parser = argparse.ArgumentParser(description="Rebuild the local SQLite database as schema v4.")
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
    return SchemaReport(
        int(connection.execute("PRAGMA user_version").fetchone()[0]),
        tuple(sorted(expected - tables)), tuple(missing_columns),
        tuple(sorted(name for name in named if name not in ddl)),
    )


def validate(connection):
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("SQLite integrity_check failed")
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"SQLite foreign_key_check found {len(violations)} violation(s)")
    report = inspect_schema(connection)
    if not report.is_current:
        raise RuntimeError(f"schema v4 validation failed: {report}")


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
    return database.with_name(f"{database.stem}.before-schema-v4-{stamp}-{uuid.uuid4().hex[:6]}.db")


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

    temporary = database_path.with_name(f".{database_path.stem}.v4-{uuid.uuid4().hex}.db")
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
