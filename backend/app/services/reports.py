from datetime import datetime, timezone

from app.extensions import db
from app.models import InstitutionReport, User


def utc_now():
    return datetime.now(timezone.utc)


def find_subject_user(report):
    return User.query.filter_by(
        health_id=report.subject_health_id,
        real_name=report.subject_name_snapshot,
        role="user",
        is_active=True,
    ).first()


def submit_report(report):
    if report.status != "locked":
        raise ValueError("only a locked report can be submitted")
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
