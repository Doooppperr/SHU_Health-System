from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    Appointment,
    AppointmentEvent,
    BookingParticipantAuthorization,
    InstitutionReport,
    User,
)


PASSWORD = "Shuhealthdoc！"


def _login(client, username):
    response = client.post(
        "/api/auth/login",
        json=client.login_payload(username, PASSWORD),
    )
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def test_proxy_receipt_exposes_only_masked_operational_review_progress(
    client,
    app,
):
    headers = _login(client, "test1")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with app.app_context():
        booker = User.query.filter_by(username="test1").one()
        appointment = Appointment.query.join(
            BookingParticipantAuthorization,
            BookingParticipantAuthorization.appointment_id == Appointment.id,
        ).filter(
            Appointment.booked_by_user_id == booker.id,
            Appointment.user_id != booker.id,
            ~Appointment.report.has(),
            BookingParticipantAuthorization.participant_type
            == "health_code_token",
        ).order_by(Appointment.id.desc()).first()
        assert appointment is not None
        original_name = appointment.user.real_name
        appointment.status = "awaiting_report"
        appointment.attended_at = now
        report = InstitutionReport(
            institution_id=appointment.institution_id,
            created_by_user_id=appointment.institution.administrator.id,
            created_by_username_snapshot=(
                appointment.institution.administrator.username
            ),
            subject_name_snapshot=appointment.user_name_snapshot,
            subject_health_id=appointment.user_health_id_snapshot,
            exam_date=appointment.appointment_date,
            package_id=appointment.package_id,
            package_version_id=appointment.package_version_id,
            appointment_id=appointment.id,
            matched_user_id=appointment.user_id,
            status="pending_review",
            upload_doctor_name="虚构上传医生",
            submitted_for_review_at=now + timedelta(minutes=2),
        )
        db.session.add(report)
        db.session.add_all([
            AppointmentEvent(
                appointment_id=appointment.id,
                event_type="attended",
                status_snapshot="awaiting_report",
                message="不应出现在代预约回执中的内部消息",
                occurred_at=now,
            ),
            AppointmentEvent(
                appointment_id=appointment.id,
                event_type="report_uploaded",
                status_snapshot="awaiting_report",
                message="不应暴露上传医生姓名",
                occurred_at=now + timedelta(minutes=1),
            ),
            AppointmentEvent(
                appointment_id=appointment.id,
                event_type="pending_review",
                status_snapshot="awaiting_report",
                message="不应暴露复核内部说明",
                occurred_at=now + timedelta(minutes=2),
            ),
        ])
        db.session.commit()
        group_id = appointment.booking_group_id
        appointment_id = appointment.id

    response = client.get(
        "/api/booking-groups?page=1&page_size=50",
        headers=headers,
    )
    assert response.status_code == 200, response.get_json()
    group = next(
        item for item in response.get_json()["items"]
        if item["id"] == group_id
    )
    participant = next(
        item for item in group["participants"]
        if item["appointment_id"] == appointment_id
    )

    assert participant["participant_type"] == "health_code_token"
    assert participant["display_name"] != original_name
    assert participant["report_status"] == "pending_review"
    assert participant["progress_stage"] == "pending_review"
    assert participant["submitted_for_review_at"].startswith(
        (now + timedelta(minutes=2)).replace(tzinfo=None).isoformat()
    )
    assert [event["type"] for event in participant["events"]][-3:] == [
        "attended",
        "report_uploaded",
        "pending_review",
    ]
    assert all(
        set(event) == {"type", "status", "occurred_at"}
        for event in participant["events"]
    )
    assert "report_id" not in participant
    assert "upload_doctor_name" not in participant
    assert "review_doctor_name" not in participant
    assert "message" not in str(participant)
