"""Copy a HealthDoc SQLite database into an empty GaussDB/openGauss database.

The destination URL is read from ``TARGET_DATABASE_URL`` by default so a
database password does not need to appear in shell history.  The script creates
the current SQLAlchemy schema, copies rows in foreign-key order, preserves
primary keys, resets generated-id sequences, and verifies every table count.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Boolean, Date, DateTime, JSON, create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.extensions import db  # noqa: E402
import app.models  # noqa: E402,F401  Ensures every mapped table is registered.


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate the local HealthDoc SQLite data to GaussDB/openGauss."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("instance/health_system.db"),
        help="SQLite database path (default: instance/health_system.db)",
    )
    parser.add_argument(
        "--target-url",
        default=os.getenv("TARGET_DATABASE_URL", ""),
        help="Destination SQLAlchemy URL; prefer TARGET_DATABASE_URL",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Drop existing application tables before importing",
    )
    return parser.parse_args()


def _adapt_value(column, value):
    if value is None:
        return None
    if isinstance(column.type, Boolean):
        return bool(value)
    if isinstance(column.type, JSON):
        return json.loads(value) if isinstance(value, str) else value
    if isinstance(column.type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(column.type, Date) and isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _source_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _ensure_empty_destination(connection) -> None:
    populated = []
    for table in db.metadata.sorted_tables:
        count = connection.execute(
            text(f'SELECT COUNT(*) FROM "{table.name}"')
        ).scalar_one()
        if count:
            populated.append(f"{table.name}={count}")
    if populated:
        raise RuntimeError(
            "destination is not empty; rerun with --replace only when an overwrite "
            "is intentional (" + ", ".join(populated) + ")"
        )


def _drop_destination_schema(connection) -> None:
    """Remove current and legacy application tables for an explicit replacement."""
    table_names = inspect(connection).get_table_names()
    quote = connection.dialect.identifier_preparer.quote

    if connection.dialect.name == "sqlite":
        known_tables = set(db.metadata.tables)
        for table_name in reversed(table_names):
            if table_name not in known_tables:
                connection.exec_driver_sql(
                    f"DROP TABLE IF EXISTS {quote(table_name)}"
                )
        db.metadata.drop_all(bind=connection)
        return

    for table_name in reversed(table_names):
        connection.exec_driver_sql(
            f"DROP TABLE IF EXISTS {quote(table_name)} CASCADE"
        )


def _reset_sequences(connection) -> None:
    if connection.dialect.name == "sqlite":
        return
    for table in db.metadata.sorted_tables:
        primary_key = list(table.primary_key.columns)
        if len(primary_key) != 1 or not isinstance(primary_key[0].type, db.Integer):
            continue
        column = primary_key[0]
        sequence = connection.execute(
            text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": table.name, "column_name": column.name},
        ).scalar_one_or_none()
        if not sequence:
            continue
        maximum = connection.execute(
            text(f'SELECT MAX("{column.name}") FROM "{table.name}"')
        ).scalar_one()
        if maximum is None:
            connection.execute(text("SELECT setval(:sequence, 1, false)"), {"sequence": sequence})
        else:
            connection.execute(
                text("SELECT setval(:sequence, :value, true)"),
                {"sequence": sequence, "value": int(maximum)},
            )


def migrate(source_path: Path, target_url: str, replace: bool = False) -> dict[str, int]:
    source_path = source_path.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source not found: {source_path}")
    if not target_url:
        raise ValueError("set TARGET_DATABASE_URL or pass --target-url")
    if target_url.startswith("sqlite:"):
        target_path = target_url.removeprefix("sqlite:///")
        if target_path and Path(target_path).expanduser().resolve() == source_path:
            raise ValueError("source and destination must be different databases")

    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    engine = create_engine(target_url, pool_pre_ping=True)
    expected_counts: dict[str, int] = {}

    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        foreign_key_errors = source.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(
                f"SQLite foreign-key check returned {len(foreign_key_errors)} error(s)"
            )

        available = _source_tables(source)
        required = set(db.metadata.tables)
        missing = sorted(required - available)
        if missing:
            raise RuntimeError("SQLite source is missing tables: " + ", ".join(missing))

        with engine.begin() as target:
            if replace:
                _drop_destination_schema(target)
            db.metadata.create_all(bind=target)
            _ensure_empty_destination(target)

            for table in db.metadata.sorted_tables:
                rows = source.execute(f'SELECT * FROM "{table.name}"').fetchall()
                expected_counts[table.name] = len(rows)
                if not rows:
                    continue
                payload = []
                for row in rows:
                    item = {
                        column.name: _adapt_value(column, row[column.name])
                        for column in table.columns
                    }
                    payload.append(item)
                target.execute(table.insert(), payload)

            _reset_sequences(target)

            for table_name, expected in expected_counts.items():
                actual = target.execute(
                    text(f'SELECT COUNT(*) FROM "{table_name}"')
                ).scalar_one()
                if actual != expected:
                    raise RuntimeError(
                        f"row-count mismatch for {table_name}: expected {expected}, got {actual}"
                    )

        with engine.connect() as target:
            destination_tables = set(inspect(target).get_table_names())
            if not required.issubset(destination_tables):
                raise RuntimeError("destination schema validation failed")
        return expected_counts
    finally:
        source.close()
        engine.dispose()


def main() -> int:
    args = _arguments()
    counts = migrate(args.source, args.target_url, args.replace)
    print("Migration completed and verified:")
    for table_name in sorted(counts):
        print(f"  {table_name}: {counts[table_name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
