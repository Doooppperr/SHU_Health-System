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

import sqlalchemy as sa
from sqlalchemy import Boolean, Date, DateTime, JSON, create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.extensions import db  # noqa: E402
import app.models  # noqa: E402,F401  Ensures every mapped table is registered.
from app.schema import _schema_shape_issues  # noqa: E402


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


def _remap_friend_relation_references(
    connection,
    *,
    primary_id: int,
    duplicate_ids: list[int],
) -> None:
    """Keep imported booking/waitlist evidence attached to the retained pair."""

    if not duplicate_ids:
        return
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for table_name in (
        "booking_participant_authorizations",
        "waitlist_subscription_participants",
    ):
        if table_name not in tables:
            continue
        columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        if "friend_relation_id" not in columns:
            continue
        connection.execute(
            text(
                f"UPDATE {table_name} "
                "SET friend_relation_id=:primary_id "
                "WHERE friend_relation_id IN :duplicate_ids"
            ).bindparams(sa.bindparam("duplicate_ids", expanding=True)),
            {
                "primary_id": primary_id,
                "duplicate_ids": duplicate_ids,
            },
        )


def _upgrade_legacy_rows_to_v12(connection) -> dict[str, int]:
    """Backfill v12 semantics after copying a v11-or-older snapshot.

    The generic table copier deliberately copies only common columns.  The
    resulting database therefore needs the semantic migration below; schema
    shape validation alone would otherwise leave all existing users
    unverified, all friend links one-way, and all existing appointments
    without a participant authorization record.
    """

    connection.execute(
        text(
            "UPDATE users "
            "SET identity_completed_at=COALESCE(created_at, CURRENT_TIMESTAMP) "
            "WHERE role='user' "
            "AND real_name IS NOT NULL AND length(trim(real_name)) > 0 "
            "AND birth_date IS NOT NULL "
            "AND gender IN ('male','female','other','undisclosed')"
        )
    )
    # Credentials created before the user/client security-epoch snapshots
    # existed cannot be trusted. Preserve their rows for audit while making
    # every outstanding authorization code and token terminal.
    connection.execute(
        text(
            "UPDATE oauth_authorization_codes "
            "SET consumed_at=COALESCE(consumed_at, CURRENT_TIMESTAMP)"
        )
    )
    connection.execute(
        text(
            "UPDATE oauth_access_tokens "
            "SET revoked_at=COALESCE(revoked_at, CURRENT_TIMESTAMP)"
        )
    )
    connection.execute(
        text(
            "UPDATE oauth_refresh_tokens "
            "SET revoked_at=COALESCE(revoked_at, CURRENT_TIMESTAMP)"
        )
    )

    friend_rows = connection.execute(
        text(
            "SELECT id, user_id, friend_user_id, relation_name, auth_status, "
            "booking_auth_status, booking_authorized_at, created_at "
            "FROM friend_relations ORDER BY id"
        )
    ).mappings().all()
    grouped: dict[tuple[int, int], list] = {}
    for row in friend_rows:
        key = tuple(sorted((int(row["user_id"]), int(row["friend_user_id"]))))
        grouped.setdefault(key, []).append(row)
    for (low, high), rows in grouped.items():
        primary = rows[0]
        active = any(
            bool(row["auth_status"]) or bool(row["booking_auth_status"])
            for row in rows
        )
        forward_name = None
        reverse_name = None
        authorization_times = []
        for row in rows:
            if (
                int(row["user_id"]) == int(primary["user_id"])
                and int(row["friend_user_id"]) == int(primary["friend_user_id"])
            ):
                forward_name = forward_name or row["relation_name"]
            else:
                reverse_name = reverse_name or row["relation_name"]
            candidate = row["booking_authorized_at"] or row["created_at"]
            if candidate is not None:
                authorization_times.append(candidate)
        duplicate_ids = [int(row["id"]) for row in rows[1:]]
        if duplicate_ids:
            _remap_friend_relation_references(
                connection,
                primary_id=int(primary["id"]),
                duplicate_ids=duplicate_ids,
            )
            connection.execute(
                text("DELETE FROM friend_relations WHERE id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True)
                ),
                {"ids": duplicate_ids},
            )
        authorized_at = min(authorization_times) if active and authorization_times else None
        connection.execute(
            text(
                "UPDATE friend_relations SET "
                "pair_key=:pair_key, relation_name=:forward_name, "
                "friend_relation_name=:reverse_name, "
                "status=:status, accepted_at=:accepted_at, revoked_at=NULL, "
                "auth_status=:active, reverse_auth_status=:active, "
                "authorization_version=0, "
                "booking_auth_status=:active, "
                "reverse_booking_auth_status=:active, "
                "booking_authorized_at=:authorized_at, "
                "reverse_booking_authorized_at=:authorized_at, "
                "booking_authorization_version=0 "
                "WHERE id=:row_id"
            ),
            {
                "pair_key": f"{low}:{high}",
                "forward_name": forward_name or "亲友",
                "reverse_name": reverse_name or "亲友",
                "status": "active" if active else "pending",
                "accepted_at": authorized_at,
                "active": active,
                "authorized_at": authorized_at,
                "row_id": int(primary["id"]),
            },
        )

    connection.execute(
        text(
            "INSERT INTO booking_participant_authorizations ("
            "appointment_id, booker_user_id, subject_user_id, "
            "participant_type, friend_relation_id, authorization_version, "
            "participant_token_id, created_at"
            ") "
            "SELECT appointment.id, "
            "COALESCE(appointment.booked_by_user_id, appointment.user_id), "
            "appointment.user_id, "
            "CASE WHEN COALESCE(appointment.booked_by_user_id, "
            "appointment.user_id)=appointment.user_id "
            "THEN 'self' ELSE 'linked_account' END, "
            "relation.id, COALESCE(relation.booking_authorization_version, 0), "
            "NULL, appointment.created_at "
            "FROM appointments AS appointment "
            "LEFT JOIN friend_relations AS relation ON relation.pair_key = "
            "CASE WHEN COALESCE(appointment.booked_by_user_id, appointment.user_id) "
            "< appointment.user_id "
            "THEN CAST(COALESCE(appointment.booked_by_user_id, appointment.user_id) "
            "AS VARCHAR) || ':' || CAST(appointment.user_id AS VARCHAR) "
            "ELSE CAST(appointment.user_id AS VARCHAR) || ':' || "
            "CAST(COALESCE(appointment.booked_by_user_id, appointment.user_id) "
            "AS VARCHAR) END "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM booking_participant_authorizations AS existing "
            "WHERE existing.appointment_id=appointment.id)"
        )
    )
    connection.execute(
        text(
            "UPDATE waitlist_subscription_participants AS participant SET "
            "participant_type=CASE WHEN participant.subject_user_id=("
            "SELECT subscription.subscriber_user_id "
            "FROM waitlist_subscriptions AS subscription "
            "WHERE subscription.id=participant.subscription_id"
            ") THEN 'self' ELSE 'linked_account' END, "
            "friend_relation_id=("
            "SELECT relation.id FROM friend_relations AS relation "
            "JOIN waitlist_subscriptions AS subscription "
            "ON subscription.id=participant.subscription_id "
            "WHERE relation.pair_key=CASE "
            "WHEN subscription.subscriber_user_id < participant.subject_user_id "
            "THEN CAST(subscription.subscriber_user_id AS VARCHAR) || ':' || "
            "CAST(participant.subject_user_id AS VARCHAR) "
            "ELSE CAST(participant.subject_user_id AS VARCHAR) || ':' || "
            "CAST(subscription.subscriber_user_id AS VARCHAR) END "
            "LIMIT 1"
            "), "
            "authorization_version=COALESCE(("
            "SELECT relation.booking_authorization_version "
            "FROM friend_relations AS relation "
            "JOIN waitlist_subscriptions AS subscription "
            "ON subscription.id=participant.subscription_id "
            "WHERE relation.pair_key=CASE "
            "WHEN subscription.subscriber_user_id < participant.subject_user_id "
            "THEN CAST(subscription.subscriber_user_id AS VARCHAR) || ':' || "
            "CAST(participant.subject_user_id AS VARCHAR) "
            "ELSE CAST(participant.subject_user_id AS VARCHAR) || ':' || "
            "CAST(subscription.subscriber_user_id AS VARCHAR) END "
            "LIMIT 1"
            "), 0)"
        )
    )
    connection.execute(
        text(
            "UPDATE institution_reports "
            "SET submitted_for_review_at=COALESCE(locked_at, created_at) "
            "WHERE status IN ('pending_review','published') "
            "AND submitted_for_review_at IS NULL"
        )
    )
    connection.execute(
        text(
            "UPDATE institution_reports "
            "SET reviewed_at=COALESCE(published_at, submitted_at, locked_at, created_at) "
            "WHERE status='published' AND reviewed_at IS NULL"
        )
    )
    return {
        "friend_relations": connection.execute(
            text("SELECT COUNT(*) FROM friend_relations")
        ).scalar_one(),
        "booking_participant_authorizations": connection.execute(
            text("SELECT COUNT(*) FROM booking_participant_authorizations")
        ).scalar_one(),
    }


def migrate(
    source_path: Path,
    target_url: str,
    replace: bool = False,
    *,
    allow_legacy_source: bool = False,
) -> dict[str, int]:
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
        legacy_source = bool(missing)
        if missing and not allow_legacy_source:
            raise RuntimeError("SQLite source is missing tables: " + ", ".join(missing))

        with engine.begin() as target:
            if replace:
                _drop_destination_schema(target)
            db.metadata.create_all(bind=target)
            _ensure_empty_destination(target)

            for table in db.metadata.sorted_tables:
                if table.name not in available:
                    expected_counts[table.name] = 0
                    continue
                order_clause = ""
                if table.name == "users":
                    user_source_columns = {
                        row[1]
                        for row in source.execute('PRAGMA table_info("users")')
                    }
                    if {
                        "managed_institution_id",
                        "is_active",
                    }.issubset(user_source_columns):
                        order_clause = (
                            " ORDER BY "
                            "CASE WHEN managed_institution_id IS NULL "
                            "THEN 1 ELSE 0 END, managed_institution_id, "
                            "CASE WHEN is_active THEN 0 ELSE 1 END, id"
                        )
                    else:
                        order_clause = " ORDER BY id"
                rows = source.execute(
                    f'SELECT * FROM "{table.name}"{order_clause}'
                ).fetchall()
                expected_counts[table.name] = len(rows)
                if not rows:
                    continue
                source_columns = set(rows[0].keys())
                payload = []
                retained_institution_ids = set()
                for row in rows:
                    item = {}
                    duplicate_institution_account = False
                    if table.name == "users" and "managed_institution_id" in row.keys():
                        managed_id = row["managed_institution_id"]
                        if managed_id is not None:
                            duplicate_institution_account = (
                                managed_id in retained_institution_ids
                            )
                            retained_institution_ids.add(managed_id)
                    for column in table.columns:
                        if column.name not in source_columns:
                            continue
                        value = row[column.name]
                        if (
                            table.name == "institution_reports"
                            and column.name == "status"
                            and value in {"withdrawn", "locked"}
                        ):
                            value = (
                                "pending_review"
                                if value == "locked"
                                else "published"
                            )
                        if table.name == "users" and duplicate_institution_account:
                            if column.name == "managed_institution_id":
                                value = None
                            elif column.name == "is_active":
                                value = False
                            elif column.name == "token_version":
                                value = int(value or 0) + 1
                        if (
                            table.name == "appointments"
                            and column.name == "status"
                            and value == "invalidated"
                        ):
                            value = "no_show"
                        item[column.name] = _adapt_value(column, value)
                    if (
                        table.name == "password_verification_challenges"
                        and "token_version_snapshot" not in source_columns
                    ):
                        # A legacy code has no trustworthy authentication
                        # epoch. Preserve the audit row while ensuring it can
                        # never be confirmed after migration.
                        item["token_version_snapshot"] = 0
                        item["consumed_at"] = (
                            _adapt_value(table.c.consumed_at, row["consumed_at"])
                            or datetime.now()
                        )
                    payload.append(item)
                target.execute(table.insert(), payload)

            if legacy_source:
                expected_counts.update(_upgrade_legacy_rows_to_v12(target))
            _reset_sequences(target)
            # A full replacement is created directly from the current ORM
            # metadata, so record the matching Alembic head. The normal
            # incremental path still reaches this revision through Alembic.
            target.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            target.execute(text("DELETE FROM alembic_version"))
            target.execute(
                text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES ('20260804_schema_v13')"
                )
            )
            if target.dialect.name == "sqlite":
                # SQLite is supported as an isolated rehearsal target even
                # though production uses openGauss. Keep its native marker in
                # sync so the same strict v13 validator can inspect the copy.
                target.exec_driver_sql("PRAGMA user_version=13")

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
            issues = _schema_shape_issues(target)
            if issues:
                preview = "; ".join(issues[:20])
                if len(issues) > 20:
                    preview += f"; and {len(issues) - 20} more"
                raise RuntimeError(
                    "destination physical schema validation failed: " + preview
                )
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
