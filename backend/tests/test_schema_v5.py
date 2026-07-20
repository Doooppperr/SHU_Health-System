import sqlite3
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.extensions import db
from app.models import (
    Appointment, BookingGroup, Institution, InstitutionReport, NotificationOutbox,
    Package, PackageChangeRequest, PackageVersion, ReportAsset, ReportIndicator,
    ReportTextResult, SelfMeasurement, User, WaitlistSubscription,
)
from app import schema as schema_module
from app.schema import CURRENT_SCHEMA_VERSION


def test_schema_v7_uses_domains_booking_groups_and_private_health_assets(app):
    with app.app_context():
        assert CURRENT_SCHEMA_VERSION == 7
        assert {"self_measurements", "institution_reports", "report_indicators", "appointments", "package_change_requests"} <= set(db.metadata.tables)
        assert "exam_registrations" not in db.metadata.tables
        assert "health_records" not in db.metadata.tables
        assert "health_indicators" not in db.metadata.tables
        connection = db.session.connection()
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        assert "withdrawn_at" not in InstitutionReport.__table__.columns
        allowed_statuses = str(next(
            constraint.sqltext
            for constraint in InstitutionReport.__table__.constraints
            if constraint.name == "ck_institution_reports_status"
        ))
        assert "withdrawn" not in allowed_statuses


def test_non_sqlite_schema_validation_uses_reflection_instead_of_sqlite_master(
    app, monkeypatch
):
    class NonSqliteConnection:
        dialect = type("Dialect", (), {"name": "opengauss"})()

        @staticmethod
        def exec_driver_sql(_statement):
            raise AssertionError("non-SQLite validation queried sqlite_master")

    class MetadataInspector:
        @staticmethod
        def get_table_names():
            return list(db.metadata.tables)

        @staticmethod
        def get_columns(table_name):
            return [{"name": column.name} for column in db.metadata.tables[table_name].columns]

        @staticmethod
        def get_check_constraints(table_name):
            return [
                {"name": constraint.name}
                for constraint in db.metadata.tables[table_name].constraints
                if constraint.name
            ]

        @staticmethod
        def get_unique_constraints(table_name):
            return [
                {
                    "name": constraint.name,
                    "column_names": [column.name for column in constraint.columns],
                }
                for constraint in db.metadata.tables[table_name].constraints
                if constraint.__visit_name__ == "unique_constraint"
            ]

        @staticmethod
        def get_foreign_keys(_table_name):
            return []

        @staticmethod
        def get_pk_constraint(_table_name):
            return {}

    monkeypatch.setattr(schema_module, "inspect", lambda _connection: MetadataInspector())

    with app.app_context():
        assert schema_module._schema_shape_issues(NonSqliteConnection()) == []


def test_rebuild_preserves_only_admin_identity_and_password(tmp_path):
    from scripts.upgrade_local_database import rebuild_database

    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, email TEXT, phone TEXT, role TEXT, created_at TEXT)")
    connection.execute("INSERT INTO users VALUES (7, 'admin', 'unchanged-hash', 'a@example.com', NULL, 'admin', '2020-01-01')")
    connection.execute("INSERT INTO users VALUES (8, 'legacy-user', 'discard-me', NULL, NULL, 'user', '2020-01-01')")
    connection.commit(); connection.close()
    backup = rebuild_database(path)
    assert backup and backup.exists()
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
    assert connection.execute("SELECT id, username, password_hash FROM users").fetchall() == [(7, "admin", "unchanged-hash")]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_deleting_institution_account_retains_report_snapshot(app, client):
    admin = login(client, "admin", "admin123")
    with app.app_context():
        staff = User.query.filter_by(username="institution1_staff1").first()
        report = InstitutionReport.query.filter_by(created_by_user_id=staff.id).first()
        staff_id, report_id, username = staff.id, report.id, staff.username
    response = client.delete(f"/api/admin/institution-accounts/{staff_id}", headers=admin)
    assert response.status_code == 200
    with app.app_context():
        report = db.session.get(InstitutionReport, report_id)
        assert report.created_by_user_id is None
        assert report.created_by_username_snapshot == username


def test_demo_seed_has_rich_timelines_and_complete_role_matrix(app):
    with app.app_context():
        people = User.query.filter_by(role="user").order_by(User.username).all()
        assert [user.username for user in people] == [
            "test1", "test2", "test3", "test4", "test5"
        ]
        assert User.query.filter_by(role="institution_admin").count() == 6
        assert User.query.filter_by(username="demo_admin", role="admin").count() == 1
        assert Institution.query.count() == 3
        assert Package.query.count() == 9
        assert PackageVersion.query.count() == 10
        assert SelfMeasurement.query.count() >= 50
        assert InstitutionReport.query.filter_by(status="published").count() >= 6
        assert ReportIndicator.query.count() >= 20
        assert ReportTextResult.query.count() >= 5
        assert ReportAsset.query.count() >= 3
        assert BookingGroup.query.filter_by(party_size=3).count() >= 1
        assert WaitlistSubscription.query.count() >= 3
        assert NotificationOutbox.query.filter_by(event_type="waitlist_available").count() >= 1
        for user in people:
            assert SelfMeasurement.query.filter_by(user_id=user.id).count() >= 10
            assert InstitutionReport.query.filter_by(
                matched_user_id=user.id, status="published"
            ).count() >= 1


def test_v5_upgrade_preserves_all_current_data(tmp_path):
    from scripts.upgrade_local_database import rebuild_database

    path = tmp_path / "schema-v4.db"
    source = Path(__file__).resolve().parents[1] / "instance" / "health_system.db"
    shutil.copy2(source, path)
    connection = sqlite3.connect(path)
    expected_appointments = connection.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    expected_requests = connection.execute("SELECT COUNT(*) FROM package_change_requests").fetchone()[0]
    expected_users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    expected_reports = connection.execute("SELECT COUNT(*) FROM institution_reports").fetchone()[0]
    connection.execute("PRAGMA user_version=5")
    connection.commit()
    connection.close()

    backup = rebuild_database(path)

    assert backup and backup.exists()
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert connection.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == expected_appointments
        assert connection.execute("SELECT COUNT(*) FROM package_change_requests").fetchone()[0] == expected_requests
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == expected_users
        assert connection.execute("SELECT COUNT(*) FROM institution_reports").fetchone()[0] == expected_reports
        assert connection.execute("SELECT COUNT(*) FROM institution_reports WHERE status='published'").fetchone()[0] == expected_reports
        assert "withdrawn_at" not in {
            row[1] for row in connection.execute("PRAGMA table_info('institution_reports')")
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_full_snapshot_migration_preserves_direct_report_ownership(app, tmp_path):
    from scripts.migrate_sqlite_to_gaussdb import migrate

    source_path = tmp_path / "source.db"
    target_path = tmp_path / "target.db"
    legacy_target = sqlite3.connect(target_path)
    legacy_target.execute(
        "CREATE TABLE health_records (id INTEGER PRIMARY KEY, legacy_value TEXT)"
    )
    legacy_target.execute(
        "INSERT INTO health_records (legacy_value) VALUES ('must be replaced')"
    )
    legacy_target.commit()
    legacy_target.close()
    with app.app_context():
        expected_reports = InstitutionReport.query.count()
        source = sqlite3.connect(source_path)
        raw_connection = db.engine.raw_connection()
        try:
            raw_connection.driver_connection.backup(source)
        finally:
            source.close()
            raw_connection.close()

    counts = migrate(
        source_path,
        f"sqlite:///{target_path.as_posix()}",
        replace=True,
    )
    assert "exam_registrations" not in counts
    assert counts["institution_reports"] == expected_reports

    connection = sqlite3.connect(target_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'health_records'"
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        published_without_owner = connection.execute(
            "SELECT COUNT(*) FROM institution_reports "
            "WHERE status = 'published' AND matched_user_id IS NULL"
        ).fetchone()[0]
        assert published_without_owner == 0
    finally:
        connection.close()


def login(client, username, password):
    response = client.post("/api/auth/login", json=client.login_payload(username, password))
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}
