from datetime import datetime, timezone

from app.extensions import db
from app.models import InstitutionReport, User


def utc_now():
    return datetime.now(timezone.utc)


def find_subject_user(report):
    if report.appointment is not None:
        return User.query.filter_by(
            id=report.appointment.user_id,
            health_id=report.subject_health_id,
            role="user",
            is_active=True,
        ).first()
    return User.query.filter_by(
        health_id=report.subject_health_id,
        real_name=report.subject_name_snapshot,
        role="user",
        is_active=True,
    ).first()


def submit_report(report):
    if report.status != "pending_review":
        raise ValueError("only a report pending review can be published")
    user = find_subject_user(report)
    if user is None:
        raise ValueError("registered user not found or identity does not match")
    now = utc_now()
    report.status = "published"
    report.matched_user_id = user.id
    report.submitted_at = now
    report.published_at = now
    db.session.flush()
    return user
