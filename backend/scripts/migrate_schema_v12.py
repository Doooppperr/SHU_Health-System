"""Apply the HealthDoc schema v12 migration without losing business rows."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from scripts.migrate_schema_v10 import CORE_TABLES  # noqa: E402
from app.extensions import db  # noqa: E402
import app.models  # noqa: E402,F401
from app.schema import _schema_shape_issues  # noqa: E402
from scripts.migrate_schema_v11 import (  # noqa: E402
    REQUIRED_V11_TABLES as AGENT_V11_TABLES,
)


REVISION = "20260730_schema_v12"
V11_REVISION = "20260729_schema_v11"
SUPPORTED_REVISIONS = {
    V11_REVISION,
    REVISION,
}
REQUIRED_V12_TABLES = {
    "appointment_complaints",
    "complaint_events",
    "complaint_messages",
    "comment_sanctions",
    "comment_appeals",
    "institution_audience_insight_cache",
    "delegation_session_audits",
    "delegated_action_audits",
    "booking_participant_tokens",
    "booking_participant_authorizations",
}
REQUIRED_V12_COLUMNS = {
    "users": {
        "identity_completed_at",
        "allow_health_id_proxy_booking",
        "booking_authorization_version",
        "must_change_initial_password",
    },
    "friend_relations": {
        "pair_key",
        "friend_relation_name",
        "status",
        "accepted_at",
        "revoked_at",
    },
    "institution_reports": {
        "upload_doctor_name",
        "review_doctor_name",
        "submitted_for_review_at",
        "reviewed_at",
    },
    "notification_outbox": {"sensitive_payload_cleared_at"},
    "password_verification_challenges": {"token_version_snapshot"},
    "oauth_clients": {"approval_version"},
    "oauth_authorization_codes": {
        "user_token_version_snapshot",
        "client_approval_version_snapshot",
    },
    "oauth_access_tokens": {
        "user_token_version_snapshot",
        "client_approval_version_snapshot",
    },
    "oauth_refresh_tokens": {
        "user_token_version_snapshot",
        "client_approval_version_snapshot",
    },
}

V12_ADDED_COLUMNS = {
    "users": {
        "identity_completed_at",
        "allow_health_id_proxy_booking",
        "booking_authorization_version",
        "must_change_initial_password",
    },
    "notification_outbox": {"sensitive_payload_cleared_at"},
    "password_verification_challenges": {"token_version_snapshot"},
    "oauth_clients": {"approval_version"},
    "oauth_authorization_codes": {
        "user_token_version_snapshot",
        "client_approval_version_snapshot",
    },
    "oauth_access_tokens": {
        "user_token_version_snapshot",
        "client_approval_version_snapshot",
    },
    "oauth_refresh_tokens": {
        "user_token_version_snapshot",
        "client_approval_version_snapshot",
    },
    "friend_relations": {
        "pair_key",
        "friend_relation_name",
        "status",
        "reverse_auth_status",
        "authorization_version",
        "reverse_booking_auth_status",
        "reverse_booking_authorized_at",
        "booking_authorization_version",
        "accepted_at",
        "revoked_at",
    },
    "waitlist_subscription_participants": {
        "participant_type",
        "friend_relation_id",
        "authorization_version",
    },
    "institutions": {"account_deactivated_at"},
    "institution_reports": {
        "upload_doctor_name",
        "review_doctor_name",
        "submitted_for_review_at",
        "reviewed_by_user_id",
        "reviewed_by_username_snapshot",
        "reviewed_at",
    },
    "comments": {"hidden_reason", "moderated_by_user_id", "moderated_at"},
}
V11_REQUIRED_TABLES = set(db.metadata.tables) - REQUIRED_V12_TABLES


def _upgrade_stamped_v12_password_challenges(connection) -> None:
    """Complete additive security columns in already-stamped v12 builds.

    The round-six branch produced development snapshots before the challenge
    and OAuth epoch fields were finalized. Alembic will not revisit an already
    stamped revision, so repair only those additive columns and invalidate the
    legacy credentials that lack a trustworthy epoch.
    """

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    is_current = False
    if "alembic_version" in tables:
        is_current = connection.execute(
            text(
                "SELECT COUNT(*) FROM alembic_version "
                "WHERE version_num=:revision"
            ),
            {"revision": REVISION},
        ).scalar_one() > 0
    if connection.dialect.name == "sqlite":
        is_current = is_current or (
            connection.exec_driver_sql("PRAGMA user_version").scalar_one()
            == 12
        )
    if not is_current:
        return
    if "password_verification_challenges" in tables:
        challenge_columns = {
            column["name"]
            for column in inspector.get_columns(
                "password_verification_challenges"
            )
        }
        if "token_version_snapshot" not in challenge_columns:
            connection.execute(
                text(
                    "ALTER TABLE password_verification_challenges "
                    "ADD COLUMN token_version_snapshot INTEGER "
                    "NOT NULL DEFAULT 0"
                )
            )
            connection.execute(
                text(
                    "UPDATE password_verification_challenges "
                    "SET token_version_snapshot=COALESCE(("
                    "SELECT users.token_version FROM users "
                    "WHERE users.id=password_verification_challenges.user_id"
                    "), 0), consumed_at=COALESCE("
                    "consumed_at, CURRENT_TIMESTAMP)"
                )
            )

    oauth_changed = False
    oauth_columns = {
        "oauth_clients": ("approval_version",),
        "oauth_authorization_codes": (
            "user_token_version_snapshot",
            "client_approval_version_snapshot",
        ),
        "oauth_access_tokens": (
            "user_token_version_snapshot",
            "client_approval_version_snapshot",
        ),
        "oauth_refresh_tokens": (
            "user_token_version_snapshot",
            "client_approval_version_snapshot",
        ),
    }
    for table_name, required_columns in oauth_columns.items():
        if table_name not in tables:
            continue
        present = {
            column["name"]
            for column in inspect(connection).get_columns(table_name)
        }
        for column_name in required_columns:
            if column_name in present:
                continue
            connection.execute(
                text(
                    f'ALTER TABLE "{table_name}" ADD COLUMN '
                    f'"{column_name}" INTEGER NOT NULL DEFAULT 0'
                )
            )
            oauth_changed = True
    if oauth_changed:
        for table_name, terminal_column in (
            ("oauth_authorization_codes", "consumed_at"),
            ("oauth_access_tokens", "revoked_at"),
            ("oauth_refresh_tokens", "revoked_at"),
        ):
            if table_name in tables:
                connection.execute(
                    text(
                        f'UPDATE "{table_name}" SET "{terminal_column}"='
                        f'COALESCE("{terminal_column}", CURRENT_TIMESTAMP)'
                    )
                )


def _normalize_development_identity_column(connection) -> None:
    """Normalize the short-lived development v12 column name.

    This runs before the current-schema fast path, including for databases
    already stamped at the v12 revision.  The operation is transactional on
    both SQLite and openGauss/PostgreSQL-compatible targets.
    """

    inspector = inspect(connection)
    if "users" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "profile_completed_at" not in columns:
        return
    if "identity_completed_at" not in columns:
        connection.execute(
            text(
                "ALTER TABLE users RENAME COLUMN profile_completed_at "
                "TO identity_completed_at"
            )
        )
        return
    connection.execute(
        text(
            "UPDATE users SET identity_completed_at="
            "COALESCE(identity_completed_at, profile_completed_at)"
        )
    )
    connection.execute(text("ALTER TABLE users DROP COLUMN profile_completed_at"))


def _counts(connection) -> dict[str, int]:
    tables = set(inspect(connection).get_table_names())
    return {
        name: connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
        for name in CORE_TABLES
        if name in tables
    }


def _has_current_v12_schema(connection) -> bool:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if _schema_shape_issues(connection):
        return False
    user_columns = {
        column["name"] for column in inspector.get_columns("users")
    }
    return "profile_completed_at" not in user_columns


def _revision_rows(connection) -> list[str]:
    if "alembic_version" not in inspect(connection).get_table_names():
        return []
    rows = connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalars().all()
    if len(rows) != 1:
        raise RuntimeError(
            "alembic_version must contain exactly one revision row"
        )
    revision = str(rows[0])
    if revision not in SUPPORTED_REVISIONS:
        raise RuntimeError(f"unsupported alembic revision: {revision}")
    return [revision]


def _verify_v11_schema(connection) -> None:
    """Fail closed unless the database has the complete pre-v12 contract."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    missing_tables = sorted(V11_REQUIRED_TABLES - tables)
    unexpected_tables = sorted(REQUIRED_V12_TABLES & tables)
    if missing_tables or unexpected_tables:
        raise RuntimeError(
            "unversioned/schema-v11 contract mismatch: "
            f"missing_tables={missing_tables}, "
            f"unexpected_v12_tables={unexpected_tables}"
        )
    if not AGENT_V11_TABLES.issubset(tables):
        raise RuntimeError("schema-v11 Agent/OAuth tables are incomplete")

    missing_columns = {}
    unexpected_v12_columns = {}
    for table_name in sorted(V11_REQUIRED_TABLES):
        actual = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        expected = set(db.metadata.tables[table_name].columns.keys()) - (
            V12_ADDED_COLUMNS.get(table_name) or set()
        )
        missing = sorted(expected - actual)
        unexpected = sorted(
            (V12_ADDED_COLUMNS.get(table_name) or set()) & actual
        )
        if missing:
            missing_columns[table_name] = missing
        if unexpected:
            unexpected_v12_columns[table_name] = unexpected
    if missing_columns or unexpected_v12_columns:
        raise RuntimeError(
            "schema-v11 column contract mismatch: "
            f"missing={missing_columns}, "
            f"unexpected_v12={unexpected_v12_columns}"
        )

    required_checks = {
        "users": {"ck_users_role_institution_binding"},
        "institution_reports": {"ck_institution_reports_status"},
    }
    for table_name, expected_names in required_checks.items():
        if table_name not in V11_REQUIRED_TABLES:
            continue
        present = {
            item.get("name")
            for item in inspector.get_check_constraints(table_name)
        }
        if not expected_names.issubset(present):
            raise RuntimeError(
                f"schema-v11 constraint contract mismatch: {table_name} "
                f"missing={sorted(expected_names - present)}"
            )
    # ``booked_by_user_id`` was introduced as nullable in schema v7.  A null
    # value on a historical appointment means a self-booking, and the v12
    # migration deliberately materializes that fallback in the authorization
    # snapshot instead of rejecting an otherwise complete v11 database.


def _stamp_revision(connection, revision: str) -> None:
    tables = set(inspect(connection).get_table_names())
    if "alembic_version" not in tables:
        connection.execute(
            text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES (:revision)"
            ),
            {"revision": revision},
        )
        return
    rows = _revision_rows(connection)
    if rows != [revision]:
        raise RuntimeError(
            f"refusing to overwrite alembic revision {rows} with {revision}"
        )


def _stamp_current_v12(connection) -> None:
    if not _has_current_v12_schema(connection):
        raise RuntimeError("refusing to stamp an incomplete schema as v12")
    _stamp_revision(connection, REVISION)


def _migrate_sqlite_v11_copy_on_write(database_url: str) -> bool:
    """Safely migrate a complete SQLite v11 database without ALTER emulation.

    SQLite cannot transactionally drop the v11 named constraints used by the
    Alembic revision.  Reusing that path can therefore leave a half-upgraded
    file.  The local upgrader builds and validates a separate v12 file first;
    only then does it atomically replace the original and retain a recovery
    copy.  This helper returns ``False`` for revisions that belong to the
    normal Alembic path.
    """

    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() != "sqlite":
        return False
    raw_database = parsed_url.database
    if not raw_database or raw_database == ":memory:":
        raise RuntimeError(
            "schema-v11 SQLite migration requires a filesystem database"
        )
    if parsed_url.query:
        raise RuntimeError(
            "schema-v11 SQLite migration does not accept URL query options"
        )
    database_path = Path(raw_database).resolve()

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            rows = _revision_rows(connection)
            if rows and rows != [V11_REVISION]:
                return False
            if not rows and _has_current_v12_schema(connection):
                return False
            if connection.exec_driver_sql(
                "PRAGMA user_version"
            ).scalar_one() != 11:
                return False
            _verify_v11_schema(connection)
            before = _counts(connection)
    finally:
        engine.dispose()

    from scripts.upgrade_local_database import rebuild_database

    backup = rebuild_database(database_path)
    if backup is None:
        raise RuntimeError(
            "schema-v11 SQLite migration did not create a recovery backup"
        )
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                _stamp_current_v12(connection)
                if _revision_rows(connection) != [REVISION]:
                    raise RuntimeError(
                        "SQLite schema-v12 revision stamp verification failed"
                    )
                after = _counts(connection)
                if before != after:
                    raise RuntimeError(
                        "core data counts changed during SQLite migration: "
                        f"before={before}, after={after}"
                    )
        finally:
            engine.dispose()
    except Exception:
        shutil.copy2(backup, database_path)
        raise

    print("sqlite_migration=copy_on_write")
    print(f"recovery_backup={backup}")
    print(f"core_data_counts={before}")
    return True


def migrate(database_url: str) -> None:
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    configured_url = os.environ.get("DATABASE_URL")
    if not configured_url or configured_url != database_url:
        raise RuntimeError(
            "DATABASE_URL argument must exactly match the process environment"
        )
    if _migrate_sqlite_v11_copy_on_write(database_url):
        return

    baseline_stamped = False
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            rows = _revision_rows(connection)
            if not rows:
                if _has_current_v12_schema(connection):
                    _stamp_current_v12(connection)
                    print("alembic_baseline=stamped_from_verified_schema_v12")
                else:
                    _verify_v11_schema(connection)
                    _stamp_revision(connection, V11_REVISION)
                    baseline_stamped = True
                    print("alembic_baseline=stamped_from_verified_schema_v11")
                rows = _revision_rows(connection)
            if rows == [REVISION]:
                _normalize_development_identity_column(connection)
                _upgrade_stamped_v12_password_challenges(connection)
                if not _has_current_v12_schema(connection):
                    raise RuntimeError(
                        "schema is stamped v12 but fails the complete contract"
                    )
                print(f"core_data_counts={_counts(connection)}")
                return
            if rows == [V11_REVISION]:
                _verify_v11_schema(connection)
                baseline_stamped = True
    finally:
        engine.dispose()

    if not baseline_stamped:
        raise RuntimeError(
            "schema-v12 migration only accepts a verified schema-v11 source"
        )
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            if _revision_rows(connection) != [V11_REVISION]:
                raise RuntimeError("schema-v11 preparation did not set its revision")
            before = _counts(connection)
    finally:
        engine.dispose()

    previous_flag = os.environ.get("HEALTHDOC_SCHEMA_MIGRATION")
    os.environ["HEALTHDOC_SCHEMA_MIGRATION"] = "1"
    try:
        from app import create_app
        from flask_migrate import upgrade

        app = create_app("development")
        with app.app_context():
            upgrade(
                directory=str(BACKEND_DIR / "migrations"),
                revision=REVISION,
            )
    finally:
        if previous_flag is None:
            os.environ.pop("HEALTHDOC_SCHEMA_MIGRATION", None)
        else:
            os.environ["HEALTHDOC_SCHEMA_MIGRATION"] = previous_flag

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            missing = REQUIRED_V12_TABLES - tables
            if missing:
                raise RuntimeError(
                    f"schema v12 verification failed: missing={sorted(missing)}"
                )
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            if revision != REVISION:
                raise RuntimeError(
                    f"schema v12 revision mismatch: expected={REVISION}, actual={revision}"
                )
            if not _has_current_v12_schema(connection):
                issues = _schema_shape_issues(connection)
                user_columns = {
                    column["name"]
                    for column in inspector.get_columns("users")
                }
                if "profile_completed_at" in user_columns:
                    issues.append("legacy column users.profile_completed_at remains")
                preview = "; ".join(issues[:20])
                if len(issues) > 20:
                    preview += f"; and {len(issues) - 20} more"
                raise RuntimeError(
                    "schema v12 full contract verification failed: " + preview
                )
            after = _counts(connection)
            if before != after:
                raise RuntimeError(
                    f"core data counts changed: before={before}, after={after}"
                )
            print(f"core_data_counts={after}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    migrate(os.environ.get("DATABASE_URL", ""))
    print("schema_v12_migration=ok")
