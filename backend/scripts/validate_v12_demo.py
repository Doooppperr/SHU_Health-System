"""Validate the complete schema-v12 acceptance dataset.

The v10 validator remains the baseline for clinical/report/media consistency.
This module runs it first, then adds strict round-six identity, delegation,
booking, report-review, complaint, moderation and audience-profile checks.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from validate_v10_demo import (  # noqa: E402
    create_read_only_validation_app,
    main as validate_v10_demo,
)

from app.config import config_by_name  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    Appointment,
    AppointmentComplaint,
    BookingGroup,
    BookingParticipantAuthorization,
    BookingParticipantToken,
    CommentAppeal,
    CommentSanction,
    ComplaintMessage,
    FriendRelation,
    IndicatorDict,
    Institution,
    InstitutionReport,
    ReportIndicator,
    SelfMeasurement,
    User,
)
from app.services.audience_insights import _aggregate  # noqa: E402


EXPECTED_ROLE_COUNTS = {
    "admin": 2,
    "institution_admin": 15,
    "user": 6,
}
EXPECTED_FIXED_USERNAMES = {
    *(f"test{index}" for index in range(1, 7)),
    *(f"institution{index}_staff1" for index in range(1, 16)),
}
REQUIRED_REPORT_STATUSES = {"draft", "pending_review", "published"}
REQUIRED_COMPLAINT_STATUSES = {
    "institution_pending",
    "user_confirmation",
    "platform_pending",
    "platform_processing",
    "resolved",
}
REQUIRED_APPEAL_STATUSES = {"pending", "approved", "rejected"}
REQUIRED_PARTICIPANT_TYPES = {
    "self",
    "linked_account",
    "health_code_token",
}
REQUIRED_V12_TABLES = {
    "appointment_complaints",
    "booking_participant_authorizations",
    "booking_participant_tokens",
    "comment_appeals",
    "comment_sanctions",
    "complaint_events",
    "complaint_messages",
    "delegated_action_audits",
    "delegation_session_audits",
    "institution_audience_insight_cache",
}


def _validate_sqlite_schema() -> dict:
    if db.engine.dialect.name != "sqlite":
        raise RuntimeError("the acceptance snapshot validator requires SQLite")
    connection = db.session.connection()
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(REQUIRED_V12_TABLES - tables)
    if missing:
        raise RuntimeError(f"schema-v12 tables are missing: {missing}")
    user_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(users)")
    }
    if "identity_completed_at" not in user_columns:
        raise RuntimeError("users.identity_completed_at is missing")
    if "profile_completed_at" in user_columns:
        raise RuntimeError(
            "development-only users.profile_completed_at must not remain physically"
        )
    challenge_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(password_verification_challenges)"
        )
    }
    if "token_version_snapshot" not in challenge_columns:
        raise RuntimeError(
            "password_verification_challenges.token_version_snapshot is missing"
        )
    oauth_security_columns = {
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
    for table_name, expected_columns in oauth_security_columns.items():
        present_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                f'PRAGMA table_info("{table_name}")'
            )
        }
        missing_columns = expected_columns - present_columns
        if missing_columns:
            raise RuntimeError(
                f"{table_name} OAuth epoch columns are missing: "
                f"{sorted(missing_columns)}"
            )
    user_version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
    integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()
    foreign_key_errors = connection.exec_driver_sql(
        "PRAGMA foreign_key_check"
    ).all()
    revision = connection.exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).scalar_one()
    if user_version != 12 or revision != "20260730_schema_v12":
        raise RuntimeError(
            "schema-v12 version marker mismatch: "
            f"user_version={user_version}, revision={revision}"
        )
    if integrity != "ok" or foreign_key_errors:
        raise RuntimeError(
            "SQLite acceptance snapshot failed storage checks: "
            f"integrity={integrity}, foreign_key_errors={len(foreign_key_errors)}"
        )
    return {
        "schema_version": user_version,
        "alembic_revision": revision,
        "sqlite_integrity": integrity,
        "sqlite_foreign_key_errors": 0,
    }


def _validate_accounts() -> dict:
    users = User.query.order_by(User.id).all()
    active_users = [row for row in users if row.is_active]
    role_counts = Counter(row.role for row in active_users)
    if role_counts != EXPECTED_ROLE_COUNTS or len(active_users) != 23:
        raise RuntimeError(
            "schema-v12 demo account matrix must be exactly "
            f"2/15/6 (admin/institution/user), found {dict(role_counts)} "
            f"across {len(active_users)} active accounts"
        )
    usernames = {row.username for row in active_users}
    missing = sorted(EXPECTED_FIXED_USERNAMES - usernames)
    if missing:
        raise RuntimeError(f"schema-v12 fixed accounts are missing: {missing}")
    if sum(row.username in {"admin", "demo_admin"} for row in active_users) != 2:
        raise RuntimeError("admin and demo_admin must both exist")

    invalid_inactive_institution_accounts = [
        row.username
        for row in users
        if row.role == "institution_admin"
        and not row.is_active
        and row.managed_institution_id is not None
    ]
    if invalid_inactive_institution_accounts:
        raise RuntimeError(
            "inactive duplicate institution accounts must be detached: "
            f"{invalid_inactive_institution_accounts}"
        )

    test6 = User.query.filter_by(username="test6", role="user").one()
    if (
        test6.profile_completed
        or test6.identity_completed_at is not None
        or test6.real_name is not None
        or test6.birth_date is not None
        or test6.gender is not None
    ):
        raise RuntimeError("test6 must remain a new user with incomplete identity")
    if (
        SelfMeasurement.query.filter_by(user_id=test6.id).count()
        or Appointment.query.filter_by(user_id=test6.id).count()
        or InstitutionReport.query.filter_by(matched_user_id=test6.id).count()
    ):
        raise RuntimeError("test6 must not have pre-existing health business data")

    branches = Institution.query.order_by(Institution.id).all()
    if len(branches) != 15:
        raise RuntimeError(f"expected 15 institution branches, found {len(branches)}")
    for branch in branches:
        accounts = User.query.filter_by(
            role="institution_admin",
            managed_institution_id=branch.id,
        ).all()
        if len(accounts) != 1 or not accounts[0].is_active:
            raise RuntimeError(
                f"institution {branch.id} must have exactly one active account"
            )
        if accounts[0].email != branch.notification_email:
            raise RuntimeError(
                f"institution {branch.id} account email is not synchronized"
            )
    return {
        "active_accounts": len(active_users),
        "retained_account_rows": len(users),
        "role_counts": dict(role_counts),
        "branches_with_one_account": len(branches),
        "test6_identity_completed": False,
    }


def _validate_proxy_intake_scenarios() -> dict:
    completed_users = User.query.filter_by(role="user", is_active=True).filter(
        User.identity_completed_at.is_not(None),
        User.allow_health_id_proxy_booking.is_(True),
    ).all()
    height = IndicatorDict.query.filter_by(code="HEIGHT").one()
    weight = IndicatorDict.query.filter_by(code="WEIGHT").one()
    indicator_by_id = {
        height.id: "HEIGHT",
        weight.id: "WEIGHT",
    }
    coverage = Counter()
    available_by_username = {}
    for user in completed_users:
        available_codes = {
            indicator_by_id[indicator_id]
            for (indicator_id,) in (
                db.session.query(SelfMeasurement.indicator_dict_id)
                .filter(
                    SelfMeasurement.user_id == user.id,
                    SelfMeasurement.indicator_dict_id.in_(indicator_by_id),
                )
                .distinct()
                .all()
            )
        }
        available_by_username[user.username] = available_codes
        coverage[len(available_codes)] += 1
    if not all(coverage[count] for count in (0, 1, 2)):
        raise RuntimeError(
            "verified proxy-booking subjects must cover latest intake states "
            f"for neither/one/both height and weight, found {dict(coverage)}"
        )
    expected_fixtures = {
        "test2": {"HEIGHT", "WEIGHT"},
        "test3": {"WEIGHT"},
        "test4": set(),
    }
    actual_fixtures = {
        username: available_by_username.get(username)
        for username in expected_fixtures
    }
    if actual_fixtures != expected_fixtures:
        raise RuntimeError(
            "proxy-booking intake fixtures drifted: "
            f"expected={expected_fixtures}, actual={actual_fixtures}"
        )
    test5 = User.query.filter_by(
        username="test5",
        role="user",
        is_active=True,
    ).one()
    if (
        test5.identity_completed_at is None
        or test5.allow_health_id_proxy_booking
    ):
        raise RuntimeError(
            "test5 must be identity-complete with health-ID proxy booking disabled"
        )
    return {
        "verified_intake_coverage": {
            "neither": coverage[0],
            "one": coverage[1],
            "both": coverage[2],
        },
        "intake_fixtures": {
            username: sorted(codes)
            for username, codes in actual_fixtures.items()
        },
        "proxy_booking_disabled_user": "test5",
    }


def _validate_test1_indicators() -> dict:
    test1 = User.query.filter_by(username="test1", role="user").one()
    indicator_ids = {
        row_id
        for (row_id,) in (
            db.session.query(ReportIndicator.indicator_dict_id)
            .join(InstitutionReport)
            .filter(
                InstitutionReport.matched_user_id == test1.id,
                InstitutionReport.status == "published",
            )
            .distinct()
            .all()
        )
    }
    dictionary_ids = {row.id for row in IndicatorDict.query.all()}
    if len(dictionary_ids) != 104 or indicator_ids != dictionary_ids:
        raise RuntimeError(
            "test1 must have published coverage for all 104 indicators; "
            f"dictionary={len(dictionary_ids)}, covered={len(indicator_ids)}"
        )
    return {"test1_indicator_coverage": len(indicator_ids)}


def _validate_friend_scenarios() -> dict:
    users = {
        row.username: row
        for row in User.query.filter(
            User.username.in_(("test1", "test2", "test3", "test4", "test5"))
        ).all()
    }

    def relation(first, second):
        return FriendRelation.query.filter_by(
            pair_key=FriendRelation.canonical_pair_key(
                users[first].id,
                users[second].id,
            )
        ).one()

    chain = (
        relation("test1", "test2"),
        relation("test2", "test4"),
        relation("test4", "test5"),
    )
    if not all(row.is_active for row in chain):
        raise RuntimeError("test1-test2-test4-test5 must form a three-edge active chain")
    statuses = {
        row.status
        for row in FriendRelation.query.all()
    }
    if not {"active", "pending", "revoked"} <= statuses:
        raise RuntimeError(
            f"friend scenarios require active/pending/revoked, found {sorted(statuses)}"
        )
    return {
        "active_chain_edges": 3,
        "friend_statuses": sorted(statuses),
    }


def _validate_mixed_booking_and_tokens() -> dict:
    mixed = None
    mixed_authorizations = None
    for group in BookingGroup.query.filter(
        BookingGroup.party_size >= 3
    ).order_by(BookingGroup.id):
        appointment_ids = [row.id for row in group.appointments]
        authorizations = BookingParticipantAuthorization.query.filter(
            BookingParticipantAuthorization.appointment_id.in_(appointment_ids)
        ).all()
        if {
            row.participant_type for row in authorizations
        } == REQUIRED_PARTICIPANT_TYPES:
            mixed = group
            mixed_authorizations = authorizations
            break
    if mixed is None or len(mixed_authorizations) != mixed.party_size:
        raise RuntimeError(
            "a three-person mixed self/linked-account/health-code booking is required"
        )
    incomplete_intake = [
        row.id
        for row in mixed.appointments
        if row.height_cm_snapshot is None or row.weight_kg_snapshot is None
    ]
    if incomplete_intake:
        raise RuntimeError(
            f"mixed booking appointments lack height/weight snapshots: {incomplete_intake}"
        )

    now = datetime.now(timezone.utc)
    consumed = BookingParticipantToken.query.filter(
        BookingParticipantToken.consumed_at.is_not(None)
    ).count()
    expired = BookingParticipantToken.query.filter(
        BookingParticipantToken.consumed_at.is_(None),
        BookingParticipantToken.revoked_at.is_(None),
        BookingParticipantToken.expires_at < now,
    ).count()
    revoked = BookingParticipantToken.query.filter(
        BookingParticipantToken.revoked_at.is_not(None)
    ).count()
    if min(consumed, expired, revoked) < 1:
        raise RuntimeError(
            "participant token fixtures require consumed, expired and revoked rows"
        )
    return {
        "mixed_booking_party_size": mixed.party_size,
        "mixed_participant_types": sorted(REQUIRED_PARTICIPANT_TYPES),
        "token_states": {
            "consumed": consumed,
            "expired": expired,
            "revoked": revoked,
        },
    }


def _validate_report_review_data() -> dict:
    reports = InstitutionReport.query.order_by(InstitutionReport.id).all()
    statuses = {row.status for row in reports}
    if statuses != REQUIRED_REPORT_STATUSES:
        raise RuntimeError(
            f"report states must be exactly {sorted(REQUIRED_REPORT_STATUSES)}, "
            f"found {sorted(statuses)}"
        )
    unmarked_uploaders = [
        row.id
        for row in reports
        if row.status in {"pending_review", "published"}
        and "虚构" not in str(row.upload_doctor_name or "")
    ]
    unmarked_reviewers = [
        row.id
        for row in reports
        if row.status == "published"
        and "虚构" not in str(row.review_doctor_name or "")
    ]
    if unmarked_uploaders or unmarked_reviewers:
        raise RuntimeError(
            "all demo doctors must carry an explicit fictional marker: "
            f"upload={unmarked_uploaders}, review={unmarked_reviewers}"
        )
    return {
        "report_statuses": sorted(statuses),
        "fictionally_marked_doctor_reports": sum(
            row.status != "draft" for row in reports
        ),
    }


def _validate_complaints_and_moderation() -> dict:
    complaint_statuses = {
        row.status for row in AppointmentComplaint.query.all()
    }
    if len(complaint_statuses) < 4 or not (
        REQUIRED_COMPLAINT_STATUSES <= complaint_statuses
    ):
        raise RuntimeError(
            "complaint fixtures must cover at least four states and the full "
            f"canonical workflow, found {sorted(complaint_statuses)}"
        )
    for complaint in AppointmentComplaint.query.all():
        messages = ComplaintMessage.query.filter_by(
            complaint_id=complaint.id
        ).all()
        if not messages or not any(
            row.sender_role == "user" and row.content == complaint.content
            for row in messages
        ):
            raise RuntimeError(
                f"complaint {complaint.id} is missing its initial user message"
            )
        expected = (
            ("institution_admin", complaint.institution_reply),
            ("user", complaint.escalation_reason),
            ("admin", complaint.admin_reply),
        )
        for role, content in expected:
            if content and not any(
                row.sender_role == role and row.content == content
                for row in messages
            ):
                raise RuntimeError(
                    f"complaint {complaint.id} is missing appended {role} message"
                )

    sanctions = CommentSanction.query.all()
    duration_labels = {
        "permanent" if row.duration_days is None else str(row.duration_days)
        for row in sanctions
    }
    if duration_labels != {"7", "30", "permanent"}:
        raise RuntimeError(
            "comment sanctions must include 7-day, 30-day and permanent "
            f"fixtures, found {sorted(duration_labels)}"
        )
    appeal_statuses = {row.status for row in CommentAppeal.query.all()}
    if appeal_statuses != REQUIRED_APPEAL_STATUSES:
        raise RuntimeError(
            "comment appeals must cover pending/approved/rejected, found "
            f"{sorted(appeal_statuses)}"
        )
    return {
        "complaint_statuses": sorted(complaint_statuses),
        "complaint_messages": ComplaintMessage.query.count(),
        "sanction_durations": sorted(duration_labels),
        "appeal_statuses": sorted(appeal_statuses),
    }


def _validate_audience_distribution() -> dict:
    reports = InstitutionReport.query.filter_by(status="published").all()
    aggregate = _aggregate(
        reports,
        scope="organization",
        period_days=0,
        period_start=None,
        package_catalog=[],
    )
    positive_gender_groups = [
        row for row in aggregate["gender_distribution"] if row["count"] > 0
    ]
    positive_age_groups = [
        row for row in aggregate["age_distribution"] if row["count"] > 0
    ]
    if aggregate["unique_user_count"] < 5:
        raise RuntimeError("audience profile must include at least five users")
    if len(positive_gender_groups) < 2 or len(positive_age_groups) < 3:
        raise RuntimeError(
            "audience profile lacks meaningful gender or age distribution"
        )
    if (
        len(aggregate["package_ranking"]) < 5
        or len(aggregate["branch_distribution"]) < 3
    ):
        raise RuntimeError(
            "audience profile lacks package or branch distribution"
        )
    return {
        "audience_unique_users": aggregate["unique_user_count"],
        "audience_gender_groups": len(positive_gender_groups),
        "audience_age_groups": len(positive_age_groups),
        "audience_packages": len(aggregate["package_ranking"]),
        "audience_branches": len(aggregate["branch_distribution"]),
    }


def validate_v12_contract() -> dict:
    summary = {}
    summary.update(_validate_accounts())
    summary.update(_validate_proxy_intake_scenarios())
    summary.update(_validate_test1_indicators())
    summary.update(_validate_friend_scenarios())
    summary.update(_validate_mixed_booking_and_tokens())
    summary.update(_validate_report_review_data())
    summary.update(_validate_complaints_and_moderation())
    summary.update(_validate_audience_distribution())
    return summary


def main(
    database_path: Path | None = None,
    upload_dir: Path | None = None,
) -> int:
    if database_path is not None:
        database_path = database_path.expanduser().resolve()
        config_by_name["development"].SQLALCHEMY_DATABASE_URI = (
            f"sqlite:///{database_path.as_posix()}"
        )
    upload_dir = (upload_dir or BACKEND_DIR / "uploads").expanduser().resolve()
    config_by_name["development"].UPLOAD_DIR = str(upload_dir)
    if validate_v10_demo(database_path, upload_dir) != 0:
        raise RuntimeError("schema-v10 baseline validation failed")
    app = create_read_only_validation_app()
    with app.app_context():
        summary = _validate_sqlite_schema()
        summary.update(validate_v12_contract())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate the complete schema-v12 acceptance database."
    )
    parser.add_argument(
        "--database",
        type=Path,
        help=(
            "Explicit SQLite snapshot to validate. Supplying this option "
            "prevents DATABASE_URL or LOCAL_DATABASE_URL from selecting a "
            "different database."
        ),
    )
    parser.add_argument(
        "--upload-dir",
        type=Path,
        default=BACKEND_DIR / "uploads",
        help="Report-media root paired with the acceptance database.",
    )
    arguments = parser.parse_args()
    raise SystemExit(main(arguments.database, arguments.upload_dir))
