"""Rebuild the local SQLite database from the current SQLAlchemy metadata.

SQLite cannot add most table constraints with ALTER TABLE. This script creates
a new database with the current model schema, copies every row, validates it,
backs up the original file, and atomically installs the upgraded database.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = BACKEND_DIR / "instance" / "health_system.db"
sys.path.insert(0, str(BACKEND_DIR))

from app import models as _models  # noqa: E402,F401
from app.extensions import db  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upgrade the local SQLite database to the current model schema."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"SQLite file to upgrade (default: {DEFAULT_DATABASE}).",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only report missing named constraints; do not modify the database.",
    )
    return parser.parse_args()


def expected_named_constraints() -> set[str]:
    return {
        constraint.name
        for table in db.metadata.tables.values()
        for constraint in table.constraints
        if constraint.name
    }


def database_ddl(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND sql IS NOT NULL
        """
    )
    return "\n".join(row[0] for row in rows)


def missing_constraints(connection: sqlite3.Connection) -> list[str]:
    ddl = database_ddl(connection)
    return sorted(name for name in expected_named_constraints() if name not in ddl)


def validate_database(
    connection: sqlite3.Connection, expected_counts: dict[str, int]
) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity_check failed: {integrity}")

    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise RuntimeError(
            f"SQLite foreign_key_check found {len(foreign_key_errors)} violation(s)."
        )

    missing = missing_constraints(connection)
    if missing:
        raise RuntimeError(f"Missing constraints after rebuild: {', '.join(missing)}")

    for table_name, expected_count in expected_counts.items():
        actual_count = connection.execute(
            f'SELECT COUNT(*) FROM "{table_name}"'
        ).fetchone()[0]
        if actual_count != expected_count:
            raise RuntimeError(
                f"Row count mismatch for {table_name}: "
                f"expected {expected_count}, got {actual_count}."
            )


def copy_rows(
    source: sqlite3.Connection, target: sqlite3.Connection
) -> dict[str, int]:
    source_tables = {
        row[0]
        for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    expected_counts: dict[str, int] = {}

    target.execute("PRAGMA foreign_keys=OFF")
    target.execute("BEGIN")
    try:
        for table in db.metadata.sorted_tables:
            table_name = table.name
            if table_name not in source_tables:
                raise RuntimeError(f"Source database is missing table: {table_name}")

            column_names = [column.name for column in table.columns]
            quoted_columns = ", ".join(f'"{name}"' for name in column_names)
            placeholders = ", ".join("?" for _ in column_names)
            rows = source.execute(
                f'SELECT {quoted_columns} FROM "{table_name}" ORDER BY id'
            ).fetchall()

            if rows:
                target.executemany(
                    f'INSERT INTO "{table_name}" '
                    f"({quoted_columns}) VALUES ({placeholders})",
                    rows,
                )
            expected_counts[table_name] = len(rows)
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        target.execute("PRAGMA foreign_keys=ON")

    return expected_counts


def rebuild_database(database_path: Path) -> Path:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database_path}")

    temporary_path = database_path.with_name(
        f".{database_path.stem}.upgrade-{uuid.uuid4().hex}.db"
    )
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = database_path.with_name(
        f"{database_path.stem}.before-normalization-{timestamp}.db"
    )

    source = sqlite3.connect(database_path)
    try:
        source.row_factory = sqlite3.Row
        source_fk_errors = source.execute("PRAGMA foreign_key_check").fetchall()
        if source_fk_errors:
            raise RuntimeError(
                f"Source database has {len(source_fk_errors)} foreign key violation(s)."
            )

        engine = create_engine(f"sqlite:///{temporary_path.as_posix()}")
        try:
            db.metadata.create_all(engine)
        finally:
            engine.dispose()

        target = sqlite3.connect(temporary_path)
        try:
            expected_counts = copy_rows(source, target)
            validate_database(target, expected_counts)
            target.execute("VACUUM")
        finally:
            target.close()
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        source.close()

    shutil.copy2(database_path, backup_path)
    os.replace(temporary_path, database_path)
    return backup_path


def main() -> None:
    args = parse_args()
    database_path = args.database.resolve()

    if args.check_only:
        with sqlite3.connect(database_path) as connection:
            missing = missing_constraints(connection)
        print(f"database={database_path}")
        print(f"missing_constraints={len(missing)}")
        for name in missing:
            print(name)
        return

    backup_path = rebuild_database(database_path)
    print(f"database={database_path}")
    print(f"backup={backup_path}")
    print("schema_upgrade=ok")


if __name__ == "__main__":
    main()
