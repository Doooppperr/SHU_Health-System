from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier, Event
import uuid

import pytest
from flask_jwt_extended import create_access_token

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    Appointment,
    AppointmentCapacitySlot,
    AppointmentEvent,
    BookingGroup,
    InstitutionReport,
    Package,
    PackageVersion,
    ReportTextResult,
    User,
    UserNotification,
)


def _access_token(user):
    return create_access_token(
        identity=str(user.id),
        additional_claims={
            "role": user.role,
            "token_version": user.token_version,
        },
    )


def _file_database_app(tmp_path, monkeypatch, filename):
    database_path = tmp_path / filename
    monkeypatch.setattr(
        TestingConfig,
        "SQLALCHEMY_DATABASE_URI",
        f"sqlite:///{database_path.as_posix()}",
    )
    return create_app("testing")


def _new_subject(suffix):
    user = User(
        username=f"appointment_race_{suffix}",
        email=f"appointment-race-{suffix}@example.test",
        role="user",
        health_id=f"HID-{suffix.upper():0<8}"[:12],
        real_name=f"虚构并发受检者{suffix}",
        gender="undisclosed",
        birth_date=date(1990, 1, 1),
        identity_completed_at=datetime.now(timezone.utc),
        is_active=True,
    )
    user.set_password("appointment-race-password")
    return user


def _pending_appointment(user, institution, package, day, *, group=None):
    return Appointment(
        user_id=user.id,
        institution_id=institution.id,
        package_id=package.id,
        package_version_id=package.current_version_id,
        booking_group_id=group.id if group is not None else None,
        booked_by_user_id=(group.booked_by_user_id if group is not None else user.id),
        appointment_date=day,
        active_date_key=day,
        status="unfulfilled",
        user_name_snapshot=user.real_name,
        user_health_id_snapshot=user.health_id,
        user_birth_date_snapshot=user.birth_date,
        user_gender_snapshot=user.gender,
        user_contact_snapshot=user.email,
        package_name_snapshot=package.name,
        package_price_snapshot=package.price,
    )


@pytest.mark.parametrize(
    ("institution_action", "expected_status", "expected_event"),
    (
        ("attend", "awaiting_report", "attended"),
        ("no_show", "no_show", "no_show"),
        (
            "institution_cancelled",
            "institution_cancelled",
            "institution_cancelled",
        ),
    ),
)
def test_user_cancel_and_institution_transition_have_exactly_one_winner(
    tmp_path,
    monkeypatch,
    institution_action,
    expected_status,
    expected_event,
):
    concurrent_app = _file_database_app(
        tmp_path,
        monkeypatch,
        f"appointment-{institution_action}-cas.db",
    )
    with concurrent_app.app_context():
        package = Package.query.filter_by(is_active=True).order_by(Package.id).first()
        institution = package.institution
        manager = institution.administrator
        subject = _new_subject(institution_action)
        db.session.add(subject)
        db.session.flush()
        day = datetime.now(timezone.utc).date() + timedelta(days=20)
        AppointmentCapacitySlot.query.filter_by(
            institution_id=institution.id,
            appointment_date=day,
        ).delete(synchronize_session=False)
        appointment = _pending_appointment(subject, institution, package, day)
        db.session.add(appointment)
        db.session.commit()
        appointment_id = appointment.id
        subject_id = subject.id
        subject_token = _access_token(subject)
        manager_token = _access_token(manager)

    from app.appointments import routes as appointment_routes
    from app.org import routes as org_routes

    original_cancel = appointment_routes._cancel_appointment_cas
    transition_barrier = Barrier(2)

    def synchronized_cancel(*args, **kwargs):
        transition_barrier.wait(timeout=10)
        return original_cancel(*args, **kwargs)

    monkeypatch.setattr(
        appointment_routes,
        "_cancel_appointment_cas",
        synchronized_cancel,
    )
    if institution_action == "attend":
        original_institution_transition = org_routes._attend_appointment_cas

        def synchronized_institution_transition(*args, **kwargs):
            transition_barrier.wait(timeout=10)
            return original_institution_transition(*args, **kwargs)

        monkeypatch.setattr(
            org_routes,
            "_attend_appointment_cas",
            synchronized_institution_transition,
        )
    else:
        original_institution_transition = org_routes._close_appointment_cas

        def synchronized_institution_transition(*args, **kwargs):
            transition_barrier.wait(timeout=10)
            return original_institution_transition(*args, **kwargs)

        monkeypatch.setattr(
            org_routes,
            "_close_appointment_cas",
            synchronized_institution_transition,
        )

    def cancel():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/appointments/{appointment_id}/cancel",
                headers={"Authorization": f"Bearer {subject_token}"},
            )
            return response.status_code, response.get_json()

    def transition():
        with concurrent_app.test_client() as worker_client:
            if institution_action == "attend":
                response = worker_client.post(
                    f"/api/org/appointments/{appointment_id}/attend",
                    headers={"Authorization": f"Bearer {manager_token}"},
                )
            else:
                payload = {"reason_type": institution_action}
                if institution_action == "institution_cancelled":
                    payload.update({
                        "reason_code": "equipment_failure",
                        "reason_text": "虚构设备故障并发取消说明",
                    })
                response = worker_client.post(
                    f"/api/org/appointments/{appointment_id}/close",
                    headers={"Authorization": f"Bearer {manager_token}"},
                    json=payload,
                )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        cancel_future = executor.submit(cancel)
        transition_future = executor.submit(transition)
        cancel_result = cancel_future.result(timeout=30)
        transition_result = transition_future.result(timeout=30)

    assert sorted((cancel_result[0], transition_result[0])) == [200, 409]
    conflict = cancel_result[1] if cancel_result[0] == 409 else transition_result[1]
    assert conflict["code"] == "APPOINTMENT_STATE_CONFLICT"

    with concurrent_app.app_context():
        stored = db.session.get(Appointment, appointment_id)
        events = AppointmentEvent.query.filter_by(
            appointment_id=appointment_id,
        ).all()
        slot = AppointmentCapacitySlot.query.filter_by(
            institution_id=stored.institution_id,
            appointment_date=day,
        ).first()
        assert len(events) == 1
        if cancel_result[0] == 200:
            assert stored.status == "cancelled"
            assert events[0].event_type == "cancelled"
            assert stored.active_date_key is None
            assert slot is not None and slot.revision == 1
        else:
            assert transition_result[0] == 200
            assert stored.status == expected_status
            assert events[0].event_type == expected_event
            assert stored.active_date_key == (
                day if expected_status == "awaiting_report" else None
            )
            if expected_status == "awaiting_report":
                assert slot is None
            else:
                assert slot is not None and slot.revision == 1
        expected_cancel_notice_count = int(
            transition_result[0] == 200
            and institution_action == "institution_cancelled"
        )
        assert UserNotification.query.filter_by(
            user_id=subject_id,
            event_type="appointment_institution_cancelled",
        ).count() == expected_cancel_notice_count


def test_group_cancel_rolls_back_every_member_when_attendance_wins(
    tmp_path,
    monkeypatch,
):
    concurrent_app = _file_database_app(
        tmp_path,
        monkeypatch,
        "appointment-group-cancel-cas.db",
    )
    with concurrent_app.app_context():
        package = Package.query.filter_by(is_active=True).order_by(Package.id).first()
        institution = package.institution
        manager = institution.administrator
        booker = _new_subject("groupbook")
        companion = _new_subject("groupmate")
        db.session.add_all((booker, companion))
        db.session.flush()
        day = datetime.now(timezone.utc).date() + timedelta(days=21)
        AppointmentCapacitySlot.query.filter_by(
            institution_id=institution.id,
            appointment_date=day,
        ).delete(synchronize_session=False)
        group = BookingGroup(
            group_code=str(uuid.uuid4()),
            booked_by_user_id=booker.id,
            institution_id=institution.id,
            package_id=package.id,
            package_version_id=package.current_version_id,
            appointment_date=day,
            party_size=2,
            package_name_snapshot=package.name,
            package_price_snapshot=package.price,
            domain_snapshot=[],
            notice_confirmed_at=datetime.now(timezone.utc),
        )
        db.session.add(group)
        db.session.flush()
        first = _pending_appointment(booker, institution, package, day, group=group)
        second = _pending_appointment(companion, institution, package, day, group=group)
        db.session.add_all((first, second))
        db.session.commit()
        group_id = group.id
        first_id = first.id
        second_id = second.id
        booker_token = _access_token(booker)
        manager_token = _access_token(manager)

    from app.booking_v7 import routes as booking_routes
    from app.org import routes as org_routes

    original_group_cancel = booking_routes._cancel_group_appointments_cas
    original_attend = org_routes._attend_appointment_cas
    transition_barrier = Barrier(2)

    def synchronized_group_cancel(*args, **kwargs):
        transition_barrier.wait(timeout=10)
        return original_group_cancel(*args, **kwargs)

    def synchronized_attend(*args, **kwargs):
        transition_barrier.wait(timeout=10)
        return original_attend(*args, **kwargs)

    monkeypatch.setattr(
        booking_routes,
        "_cancel_group_appointments_cas",
        synchronized_group_cancel,
    )
    monkeypatch.setattr(
        org_routes,
        "_attend_appointment_cas",
        synchronized_attend,
    )

    def cancel_group():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/booking-groups/{group_id}/cancel",
                headers={"Authorization": f"Bearer {booker_token}"},
            )
            return response.status_code, response.get_json()

    def attend_first():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/org/appointments/{first_id}/attend",
                headers={"Authorization": f"Bearer {manager_token}"},
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        cancel_future = executor.submit(cancel_group)
        attend_future = executor.submit(attend_first)
        cancel_result = cancel_future.result(timeout=30)
        attend_result = attend_future.result(timeout=30)

    assert sorted((cancel_result[0], attend_result[0])) == [200, 409]
    conflict = cancel_result[1] if cancel_result[0] == 409 else attend_result[1]
    assert conflict["code"] == "APPOINTMENT_STATE_CONFLICT"
    with concurrent_app.app_context():
        first = db.session.get(Appointment, first_id)
        second = db.session.get(Appointment, second_id)
        events = AppointmentEvent.query.filter(
            AppointmentEvent.appointment_id.in_((first_id, second_id)),
        ).all()
        slot = AppointmentCapacitySlot.query.filter_by(
            institution_id=first.institution_id,
            appointment_date=day,
        ).first()
        if cancel_result[0] == 200:
            assert (first.status, second.status) == ("cancelled", "cancelled")
            assert first.active_date_key is None
            assert second.active_date_key is None
            assert [row.event_type for row in events].count("cancelled") == 2
            assert all(row.event_type != "attended" for row in events)
            assert slot is not None and slot.revision == 1
        else:
            assert attend_result[0] == 200
            assert (first.status, second.status) == (
                "awaiting_report",
                "unfulfilled",
            )
            assert first.active_date_key == day
            assert second.active_date_key == day
            assert [row.event_type for row in events] == ["attended"]
            assert slot is None


def test_publish_wins_against_stale_text_edit_and_report_stays_immutable(
    tmp_path,
    monkeypatch,
):
    concurrent_app = _file_database_app(
        tmp_path,
        monkeypatch,
        "report-publish-edit-cas.db",
    )
    original_body = "虚构报告发布前的机构结论。"
    attempted_body = "该编辑在发布后不得写入。"
    with concurrent_app.app_context():
        packages = Package.query.filter_by(is_active=True).order_by(Package.id).all()
        package = next(
            item
            for item in packages
            if item.current_version_id
            and (
                (version := db.session.get(PackageVersion, item.current_version_id))
                is not None
            )
            and version.domains
            and not any(requirement.is_required for requirement in version.asset_requirements)
        )
        version = db.session.get(PackageVersion, package.current_version_id)
        domain_id = version.domains[0].health_domain_id
        institution = package.institution
        manager = institution.administrator
        subject = _new_subject("reportpub")
        db.session.add(subject)
        db.session.flush()
        day = datetime.now(timezone.utc).date() + timedelta(days=22)
        appointment = _pending_appointment(subject, institution, package, day)
        appointment.status = "awaiting_report"
        appointment.attended_at = datetime.now(timezone.utc)
        db.session.add(appointment)
        db.session.flush()
        report = InstitutionReport(
            institution_id=institution.id,
            appointment_id=appointment.id,
            created_by_user_id=manager.id,
            created_by_username_snapshot=manager.username,
            subject_name_snapshot=subject.real_name,
            subject_health_id=subject.health_id,
            exam_date=day,
            package_id=package.id,
            package_version_id=version.id,
            matched_user_id=subject.id,
            status="pending_review",
            upload_doctor_name="虚构上传医生",
            submitted_for_review_at=datetime.now(timezone.utc),
        )
        db.session.add(report)
        db.session.flush()
        text_result = ReportTextResult(
            report_id=report.id,
            health_domain_id=domain_id,
            title="虚构检查结论",
            body=original_body,
            source_snapshot="虚构机构结论",
            created_by_user_id=manager.id,
        )
        db.session.add(text_result)
        db.session.commit()
        report_id = report.id
        text_result_id = text_result.id
        appointment_id = appointment.id
        subject_id = subject.id
        manager_token = _access_token(manager)

    from app.org import routes as org_routes

    original_claim = org_routes._claim_editable_report_cas
    stale_edit_ready = Event()
    publish_finished = Event()

    def delayed_stale_claim(*args, **kwargs):
        # scoped_editable_report has already loaded pending_review on this
        # connection. Let publication commit before its guarded claim runs.
        stale_edit_ready.set()
        assert publish_finished.wait(timeout=20)
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(
        org_routes,
        "_claim_editable_report_cas",
        delayed_stale_claim,
    )

    def edit_text():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.patch(
                f"/api/org/health-data/{report_id}/text-results/{text_result_id}",
                headers={"Authorization": f"Bearer {manager_token}"},
                json={"body": attempted_body},
            )
            return response.status_code, response.get_json()

    def publish():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/org/reports/{report_id}/review",
                headers={"Authorization": f"Bearer {manager_token}"},
                json={"review_doctor_name": "虚构复核医生"},
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        edit_future = executor.submit(edit_text)
        assert stale_edit_ready.wait(timeout=20)
        publish_future = executor.submit(publish)
        try:
            publish_result = publish_future.result(timeout=30)
        finally:
            publish_finished.set()
        edit_result = edit_future.result(timeout=30)

    assert publish_result[0] == 200
    assert edit_result[0] == 409
    assert edit_result[1]["code"] == "REPORT_STATE_CONFLICT"
    immutable_requests = (
        ("put", f"/api/org/reports/{report_id}", {}),
        ("post", f"/api/org/reports/{report_id}/indicators", {}),
        ("put", f"/api/org/reports/{report_id}/indicators/999999", {}),
        ("delete", f"/api/org/reports/{report_id}/indicators/999999", None),
        ("post", f"/api/org/health-data/{report_id}/text-results", {}),
        (
            "patch",
            f"/api/org/health-data/{report_id}/text-results/{text_result_id}",
            {"body": "发布后的第二次虚构编辑"},
        ),
        (
            "delete",
            f"/api/org/health-data/{report_id}/text-results/{text_result_id}",
            None,
        ),
        ("post", f"/api/org/health-data/{report_id}/assets", None),
        ("patch", f"/api/org/health-data/{report_id}/assets/999999", {}),
        ("delete", f"/api/org/health-data/{report_id}/assets/999999", None),
    )
    with concurrent_app.test_client() as verification_client:
        for method, path, payload in immutable_requests:
            response = verification_client.open(
                path,
                method=method.upper(),
                headers={"Authorization": f"Bearer {manager_token}"},
                json=payload,
            )
            assert response.status_code == 409, (method, path, response.get_json())
            assert response.get_json()["code"] == "REPORT_STATE_CONFLICT"
    with concurrent_app.app_context():
        report = db.session.get(InstitutionReport, report_id)
        appointment = db.session.get(Appointment, appointment_id)
        stored_text = db.session.get(ReportTextResult, text_result_id)
        assert report.status == "published"
        assert appointment.status == "fulfilled"
        assert stored_text.body == original_body
        assert AppointmentEvent.query.filter_by(
            appointment_id=appointment_id,
            event_type="report_published",
        ).count() == 1
        assert UserNotification.query.filter_by(
            user_id=subject_id,
            idempotency_key=f"report:{report_id}:published",
        ).count() == 1
