from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, text

from scripts import migrate_schema_v12 as migration
from app.extensions import db
from scripts.migrate_sqlite_to_gaussdb import (
    _upgrade_legacy_rows_to_v12,
    _remap_friend_relation_references as remap_import_references,
)


def _load_alembic_remapper():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260730_schema_v12.py"
    )
    spec = importlib.util.spec_from_file_location(
        "healthdoc_schema_v12_revision",
        migration_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._remap_friend_relation_references


@pytest.mark.parametrize(
    "remapper",
    [_load_alembic_remapper(), remap_import_references],
)
def test_friend_pair_deduplication_remaps_historical_references(
    tmp_path,
    remapper,
):
    database_path = tmp_path / f"{remapper.__module__.replace('.', '-')}.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(
                text(
                    "CREATE TABLE friend_relations ("
                    "id INTEGER PRIMARY KEY"
                    ")"
                )
            )
            for table_name in (
                "booking_participant_authorizations",
                "waitlist_subscription_participants",
            ):
                connection.execute(
                    text(
                        f"CREATE TABLE {table_name} ("
                        "id INTEGER PRIMARY KEY, "
                        "friend_relation_id INTEGER REFERENCES "
                        "friend_relations(id) ON DELETE SET NULL"
                        ")"
                    )
                )
            connection.execute(
                text("INSERT INTO friend_relations (id) VALUES (10), (11)")
            )
            connection.execute(
                text(
                    "INSERT INTO booking_participant_authorizations "
                    "(id, friend_relation_id) VALUES (1, 11)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO waitlist_subscription_participants "
                    "(id, friend_relation_id) VALUES (1, 11)"
                )
            )

            remapper(
                connection,
                primary_id=10,
                duplicate_ids=[11],
            )
            connection.execute(
                text("DELETE FROM friend_relations WHERE id=11")
            )

            for table_name in (
                "booking_participant_authorizations",
                "waitlist_subscription_participants",
            ):
                assert connection.execute(
                    text(
                        f"SELECT friend_relation_id FROM {table_name} "
                        "WHERE id=1"
                    )
                ).scalar_one() == 10
            assert connection.execute(
                text("PRAGMA foreign_key_check")
            ).fetchall() == []
    finally:
        engine.dispose()


def test_stamped_v12_additive_security_upgrade_invalidates_legacy_secrets(
    tmp_path,
):
    database_path = tmp_path / "stamped-v12-security.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA user_version=12"))
            connection.execute(
                text(
                    "CREATE TABLE users ("
                    "id INTEGER PRIMARY KEY, token_version INTEGER NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO users (id, token_version) VALUES (7, 23)")
            )
            connection.execute(
                text(
                    "CREATE TABLE password_verification_challenges ("
                    "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
                    "consumed_at DATETIME)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO password_verification_challenges "
                    "(id, user_id, consumed_at) VALUES (1, 7, NULL)"
                )
            )
            connection.execute(
                text("CREATE TABLE oauth_clients (id INTEGER PRIMARY KEY)")
            )
            for table_name, terminal_column in (
                ("oauth_authorization_codes", "consumed_at"),
                ("oauth_access_tokens", "revoked_at"),
                ("oauth_refresh_tokens", "revoked_at"),
            ):
                connection.execute(
                    text(
                        f"CREATE TABLE {table_name} ("
                        f"id INTEGER PRIMARY KEY, {terminal_column} DATETIME)"
                    )
                )
                connection.execute(
                    text(
                        f"INSERT INTO {table_name} "
                        f"(id, {terminal_column}) VALUES (1, NULL)"
                    )
                )

            migration._upgrade_stamped_v12_password_challenges(connection)

            assert {
                column["name"]
                for column in inspect(connection).get_columns(
                    "password_verification_challenges"
                )
            } >= {"token_version_snapshot"}
            challenge = connection.execute(
                text(
                    "SELECT token_version_snapshot, consumed_at FROM "
                    "password_verification_challenges WHERE id=1"
                )
            ).one()
            assert challenge.token_version_snapshot == 23
            assert challenge.consumed_at is not None

            assert {
                column["name"]
                for column in inspect(connection).get_columns("oauth_clients")
            } >= {"approval_version"}
            for table_name, terminal_column in (
                ("oauth_authorization_codes", "consumed_at"),
                ("oauth_access_tokens", "revoked_at"),
                ("oauth_refresh_tokens", "revoked_at"),
            ):
                assert {
                    column["name"]
                    for column in inspect(connection).get_columns(table_name)
                } >= {
                    "user_token_version_snapshot",
                    "client_approval_version_snapshot",
                }
                assert connection.execute(
                    text(
                        f"SELECT {terminal_column} FROM {table_name} "
                        "WHERE id=1"
                    )
                ).scalar_one() is not None

            # The repair is intentionally safe to repeat.
            migration._upgrade_stamped_v12_password_challenges(connection)
    finally:
        engine.dispose()


def test_revision_rows_fail_closed_for_empty_multiple_and_unknown(tmp_path):
    database_path = tmp_path / "revision-contract.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            with pytest.raises(RuntimeError, match="exactly one"):
                migration._revision_rows(connection)

            connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES ('unknown_revision')"
                )
            )
            with pytest.raises(RuntimeError, match="unsupported"):
                migration._revision_rows(connection)

            connection.execute(text("DELETE FROM alembic_version"))
            connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) VALUES "
                    "(:v11), (:v12)"
                ),
                {"v11": migration.V11_REVISION, "v12": migration.REVISION},
            )
            with pytest.raises(RuntimeError, match="exactly one"):
                migration._revision_rows(connection)
    finally:
        engine.dispose()


def test_revision_stamp_never_overwrites_existing_revision(tmp_path):
    database_path = tmp_path / "revision-stamp.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            migration._stamp_revision(connection, migration.V11_REVISION)
            migration._stamp_revision(connection, migration.V11_REVISION)
            assert migration._revision_rows(connection) == [
                migration.V11_REVISION
            ]
            with pytest.raises(RuntimeError, match="refusing to overwrite"):
                migration._stamp_revision(connection, migration.REVISION)
    finally:
        engine.dispose()


def test_unversioned_v11_preflight_rejects_partial_v12_schema(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "partial-v12.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE appointment_complaints (id INTEGER PRIMARY KEY)")
            )
            monkeypatch.setattr(migration, "V11_REQUIRED_TABLES", set())
            monkeypatch.setattr(migration, "AGENT_V11_TABLES", set())
            with pytest.raises(RuntimeError, match="unexpected_v12_tables"):
                migration._verify_v11_schema(connection)
    finally:
        engine.dispose()


def test_migrate_requires_argument_to_match_database_environment(
    tmp_path,
    monkeypatch,
):
    configured = f"sqlite:///{(tmp_path / 'configured.db').as_posix()}"
    requested = f"sqlite:///{(tmp_path / 'requested.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", configured)

    with pytest.raises(RuntimeError, match="exactly match"):
        migration.migrate(requested)
    assert not (tmp_path / "requested.db").exists()


def _create_v12_database_missing_oauth_epoch(database_path):
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        db.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA user_version=12"))
            connection.execute(
                text(
                    "ALTER TABLE oauth_clients DROP COLUMN approval_version"
                )
            )
    finally:
        engine.dispose()


def test_local_additive_repair_uses_validated_copy_on_write(tmp_path):
    from scripts.upgrade_local_database import (
        inspect_schema,
        rebuild_database,
    )

    database_path = tmp_path / "additive-copy-on-write.db"
    _create_v12_database_missing_oauth_epoch(database_path)

    backup = rebuild_database(database_path)

    assert backup is not None and backup.exists()
    with sqlite3.connect(database_path) as connection:
        assert inspect_schema(connection).is_current
        assert "approval_version" in {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("oauth_clients")'
            )
        }
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_local_additive_repair_keeps_source_and_recovery_backup(
    tmp_path,
):
    from scripts.upgrade_local_database import rebuild_database

    database_path = tmp_path / "additive-copy-on-write-failure.db"
    _create_v12_database_missing_oauth_epoch(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO password_verification_challenges ("
            "id, public_id, user_id, purpose, email_snapshot, code_hash, "
            "request_ip_hash, attempt_count, expires_at, consumed_at, "
            "created_at, token_version_snapshot"
            ") VALUES (1, 'orphan-test', 999999, 'reset', "
            "'nobody@example.invalid', 'not-a-real-secret', NULL, 0, "
            "'2099-01-01 00:00:00', NULL, CURRENT_TIMESTAMP, 0)"
        )
        connection.commit()
    before_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="foreign_key_check"):
        rebuild_database(database_path)

    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before_hash
    backups = list(tmp_path.glob(
        "additive-copy-on-write-failure.before-schema-v12-*.db"
    ))
    assert len(backups) == 1
    assert backups[0].exists()
    with sqlite3.connect(database_path) as connection:
        assert "approval_version" not in {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("oauth_clients")'
            )
        }


def test_current_v12_contract_rejects_missing_index(tmp_path):
    database_path = tmp_path / "missing-index.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        db.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text("PRAGMA user_version=12"))
            expected_index = next(
                index.name
                for index in db.metadata.tables[
                    "oauth_access_tokens"
                ].indexes
                if index.name
            )
            connection.execute(text(f'DROP INDEX "{expected_index}"'))
            assert migration._has_current_v12_schema(connection) is False
    finally:
        engine.dispose()


def test_legacy_copy_invalidates_all_oauth_credentials(tmp_path):
    database_path = tmp_path / "legacy-oauth-invalidation.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        db.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users ("
                    "id, username, password_hash, role, health_id, is_active, "
                    "allow_health_id_proxy_booking, "
                    "booking_authorization_version, "
                    "must_change_initial_password, token_version, created_at"
                    ") VALUES (1, 'legacy-oauth-user', 'unused-hash', "
                    "'user', 'SHLEGACY0001', 1, 1, 0, 0, 0, "
                    "CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO oauth_clients ("
                    "client_id, client_name, redirect_uris, scopes, status, "
                    "approval_version, token_endpoint_auth_method, created_at"
                    ") VALUES ('legacy-client', 'Legacy client', '[]', '[]', "
                    "'approved', 0, 'none', CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO oauth_authorization_codes ("
                    "id, code_hash, client_id, user_id, redirect_uri, scope, "
                    "code_challenge, user_token_version_snapshot, "
                    "client_approval_version_snapshot, expires_at, created_at"
                    ") VALUES (1, :hash, 'legacy-client', 1, "
                    "'https://example.invalid/callback', 'health:read', "
                    "'challenge', 0, 0, '2099-01-01 00:00:00', "
                    "CURRENT_TIMESTAMP)"
                ),
                {"hash": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO oauth_access_tokens ("
                    "id, token_hash, client_id, user_id, scope, audience, "
                    "user_token_version_snapshot, "
                    "client_approval_version_snapshot, expires_at, created_at"
                    ") VALUES (1, :hash, 'legacy-client', 1, 'health:read', "
                    "'healthdoc', 0, 0, '2099-01-01 00:00:00', "
                    "CURRENT_TIMESTAMP)"
                ),
                {"hash": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO oauth_refresh_tokens ("
                    "id, token_hash, family_id, client_id, user_id, scope, "
                    "audience, user_token_version_snapshot, "
                    "client_approval_version_snapshot, expires_at, created_at"
                    ") VALUES (1, :hash, 'legacy-family', 'legacy-client', 1, "
                    "'health:read', 'healthdoc', 0, 0, "
                    "'2099-01-01 00:00:00', CURRENT_TIMESTAMP)"
                ),
                {"hash": "c" * 64},
            )

            _upgrade_legacy_rows_to_v12(connection)

            assert connection.execute(
                text(
                    "SELECT consumed_at FROM oauth_authorization_codes "
                    "WHERE id=1"
                )
            ).scalar_one() is not None
            assert connection.execute(
                text("SELECT revoked_at FROM oauth_access_tokens WHERE id=1")
            ).scalar_one() is not None
            assert connection.execute(
                text("SELECT revoked_at FROM oauth_refresh_tokens WHERE id=1")
            ).scalar_one() is not None
    finally:
        engine.dispose()
