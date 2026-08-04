from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from flask_jwt_extended import create_access_token

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    Appointment,
    AppointmentComplaint,
    ComplaintEvent,
    ComplaintMessage,
    Package,
    PackageChangeRequest,
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


def test_institution_reply_and_user_escalation_have_one_atomic_winner(
    tmp_path,
    monkeypatch,
):
    concurrent_app = _file_database_app(
        tmp_path,
        monkeypatch,
        "complaint-reply-escalate-cas.db",
    )
    with concurrent_app.app_context():
        appointment = (
            Appointment.query
            .outerjoin(
                AppointmentComplaint,
                AppointmentComplaint.appointment_id == Appointment.id,
            )
            .filter(AppointmentComplaint.id.is_(None))
            .order_by(Appointment.id)
            .first()
        )
        assert appointment is not None
        complainant = appointment.user
        manager = appointment.institution.administrator
        assert complainant is not None and manager is not None
        complaint = AppointmentComplaint(
            appointment_id=appointment.id,
            institution_id=appointment.institution_id,
            complainant_user_id=complainant.id,
            complainant_username_snapshot=complainant.username,
            category="service",
            content="虚构并发投诉原始内容",
            status="institution_pending",
        )
        db.session.add(complaint)
        db.session.flush()
        db.session.add(ComplaintMessage(
            complaint_id=complaint.id,
            sender_user_id=complainant.id,
            sender_role="user",
            content=complaint.content,
        ))
        db.session.add(ComplaintEvent(
            complaint_id=complaint.id,
            event_type="created",
            actor_user_id=complainant.id,
            actor_role="user",
            content=complaint.content,
        ))
        db.session.commit()
        complaint_id = complaint.id
        complainant_token = _access_token(complainant)
        manager_token = _access_token(manager)

    from app.complaints import routes as complaint_routes
    from app.org import routes as org_routes

    original_escalate_cas = complaint_routes._transition_owned_complaint_cas
    original_reply_cas = org_routes._reply_to_complaint_cas
    transition_barrier = Barrier(2)

    def synchronized_escalate_cas(*args, **kwargs):
        assert kwargs["next_status"] == "platform_pending"
        transition_barrier.wait(timeout=10)
        return original_escalate_cas(*args, **kwargs)

    def synchronized_reply_cas(*args, **kwargs):
        transition_barrier.wait(timeout=10)
        return original_reply_cas(*args, **kwargs)

    monkeypatch.setattr(
        complaint_routes,
        "_transition_owned_complaint_cas",
        synchronized_escalate_cas,
    )
    monkeypatch.setattr(
        org_routes,
        "_reply_to_complaint_cas",
        synchronized_reply_cas,
    )

    def reply():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/org/complaints/{complaint_id}/reply",
                headers={"Authorization": f"Bearer {manager_token}"},
                json={"content": "虚构机构并发回复"},
            )
            return response.status_code, response.get_json()

    def escalate():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/complaints/{complaint_id}/escalate",
                headers={"Authorization": f"Bearer {complainant_token}"},
                json={"reason": "虚构用户并发申请平台介入"},
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reply_future = executor.submit(reply)
        escalate_future = executor.submit(escalate)
        reply_result = reply_future.result(timeout=20)
        escalate_result = escalate_future.result(timeout=20)

    assert sorted((reply_result[0], escalate_result[0])) == [200, 409]
    winner_payload = (
        reply_result[1] if reply_result[0] == 200 else escalate_result[1]
    )
    conflict_payload = (
        reply_result[1] if reply_result[0] == 409 else escalate_result[1]
    )
    assert conflict_payload["code"] == "COMPLAINT_STATE_CONFLICT"

    with concurrent_app.app_context():
        stored = db.session.get(AppointmentComplaint, complaint_id)
        event_types = [row.event_type for row in stored.events]
        message_roles = [row.sender_role for row in stored.messages]
        notifications = UserNotification.query.filter(
            UserNotification.idempotency_key.like(f"complaint:{complaint_id}:%")
        ).all()
        notification_types = [row.event_type for row in notifications]

        assert event_types.count("institution_replied") + event_types.count("escalated") == 1
        assert len(message_roles) == 2
        if reply_result[0] == 200:
            assert stored.status == "user_confirmation"
            assert winner_payload["item"]["status"] == "user_confirmation"
            assert event_types.count("institution_replied") == 1
            assert message_roles.count("institution_admin") == 1
            assert notification_types.count("complaint_institution_replied") == 1
            assert "complaint_escalated" not in notification_types
        else:
            assert escalate_result[0] == 200
            assert stored.status == "platform_pending"
            assert winner_payload["item"]["status"] == "platform_pending"
            assert event_types.count("escalated") == 1
            assert message_roles.count("user") == 2
            assert "complaint_institution_replied" not in notification_types
            assert notification_types.count("complaint_escalated") >= 1


def test_package_withdraw_uses_pending_status_compare_and_swap(
    tmp_path,
    monkeypatch,
):
    concurrent_app = _file_database_app(
        tmp_path,
        monkeypatch,
        "package-withdraw-cas.db",
    )
    with concurrent_app.app_context():
        package = Package.query.filter_by(is_active=True).order_by(Package.id).first()
        assert package is not None
        manager = package.institution.administrator
        assert manager is not None
        change = PackageChangeRequest(
            institution_id=package.institution_id,
            package_id=package.id,
            action="update",
            status="pending",
            before_data={"name": package.name},
            proposed_data={"name": package.name},
            requested_by_user_id=manager.id,
        )
        db.session.add(change)
        db.session.commit()
        request_id = change.id
        manager_token = _access_token(manager)

    from app.org import routes as org_routes

    original_withdraw_cas = org_routes._withdraw_package_change_request_cas
    transition_barrier = Barrier(2)

    def synchronized_withdraw_cas(*args, **kwargs):
        transition_barrier.wait(timeout=10)
        return original_withdraw_cas(*args, **kwargs)

    monkeypatch.setattr(
        org_routes,
        "_withdraw_package_change_request_cas",
        synchronized_withdraw_cas,
    )

    def withdraw():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/org/package-change-requests/{request_id}/withdraw",
                headers={"Authorization": f"Bearer {manager_token}"},
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=20)
            for future in (executor.submit(withdraw), executor.submit(withdraw))
        ]

    assert sorted(status for status, _payload in results) == [200, 409]
    winner_payload = next(payload for status, payload in results if status == 200)
    assert winner_payload["item"]["status"] == "withdrawn"
    assert winner_payload["item"]["withdrawn_at"] is not None
    with concurrent_app.app_context():
        stored = db.session.get(PackageChangeRequest, request_id)
        assert stored.status == "withdrawn"
        assert stored.withdrawn_at is not None


def test_same_package_cannot_create_two_pending_reviews_concurrently(
    tmp_path,
    monkeypatch,
):
    concurrent_app = _file_database_app(
        tmp_path,
        monkeypatch,
        "package-pending-slot.db",
    )
    with concurrent_app.app_context():
        package = (
            Package.query
            .filter(
                Package.is_active.is_(True),
                ~Package.change_requests.any(
                    PackageChangeRequest.status == "pending"
                ),
            )
            .order_by(Package.id)
            .first()
        )
        assert package is not None
        manager = package.institution.administrator
        assert manager is not None
        package_id = package.id
        manager_token = _access_token(manager)

    from app.services import package_reviews

    original_claim = package_reviews._claim_package_change_slot
    claim_barrier = Barrier(2)

    def synchronized_claim(package):
        claim_barrier.wait(timeout=10)
        return original_claim(package)

    monkeypatch.setattr(
        package_reviews,
        "_claim_package_change_slot",
        synchronized_claim,
    )

    def request_update(price):
        with concurrent_app.test_client() as worker_client:
            response = worker_client.put(
                f"/api/org/packages/{package_id}",
                headers={"Authorization": f"Bearer {manager_token}"},
                json={"price": price},
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=20)
            for future in (
                executor.submit(request_update, 611),
                executor.submit(request_update, 622),
            )
        ]

    assert sum(status == 202 for status, _payload in results) == 1
    assert sum(status in {400, 409} for status, _payload in results) == 1
    with concurrent_app.app_context():
        pending = PackageChangeRequest.query.filter_by(
            package_id=package_id,
            status="pending",
        ).all()
        assert len(pending) == 1


def test_same_institution_cannot_create_duplicate_named_packages_concurrently(
    tmp_path,
    monkeypatch,
):
    concurrent_app = _file_database_app(
        tmp_path,
        monkeypatch,
        "package-create-name-slot.db",
    )
    with concurrent_app.app_context():
        package = Package.query.order_by(Package.id).first()
        assert package is not None
        institution = package.institution
        manager = institution.administrator
        assert manager is not None
        institution_id = institution.id
        manager_token = _access_token(manager)

    from app.services import package_reviews

    original_claim = package_reviews._claim_institution_package_creation_slot
    claim_barrier = Barrier(2)

    def synchronized_claim(institution):
        claim_barrier.wait(timeout=10)
        return original_claim(institution)

    monkeypatch.setattr(
        package_reviews,
        "_claim_institution_package_creation_slot",
        synchronized_claim,
    )

    payload = {
        "name": "虚构并发新增套餐",
        "focus_area": "虚构并发审核",
        "gender_scope": "all",
        "price": 388,
        "description": "仅用于第六轮并发验收",
    }

    def request_create():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                "/api/org/packages",
                headers={"Authorization": f"Bearer {manager_token}"},
                json=payload,
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=20)
            for future in (
                executor.submit(request_create),
                executor.submit(request_create),
            )
        ]

    assert sum(status == 201 for status, _payload in results) == 1
    assert sum(status == 400 for status, _payload in results) == 1
    with concurrent_app.app_context():
        pending = PackageChangeRequest.query.filter_by(
            institution_id=institution_id,
            action="create",
            status="pending",
        ).all()
        matches = [
            row for row in pending
            if (row.proposed_data or {}).get("name") == payload["name"]
        ]
        assert len(matches) == 1
