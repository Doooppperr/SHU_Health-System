from datetime import date, timedelta

from app.extensions import db
from app.models import Appointment, Institution, Package, PackageChangeRequest, User


PASSWORD = "Shuhealthdoc！"


def login(client, username, password=PASSWORD):
    response = client.post("/api/auth/login", json=client.login_payload(username, password))
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def booking_fixture(app, institution_index=0):
    with app.app_context():
        institution = Institution.query.order_by(Institution.id).all()[institution_index]
        package = next(item for item in institution.packages if item.is_active)
        return institution.id, package.id


def test_capacity_is_rechecked_and_cancellation_releases_the_slot(app, client):
    institution_id, package_id = booking_fixture(app)
    day = date.today() + timedelta(days=3)
    with app.app_context():
        institution = db.session.get(Institution, institution_id)
        institution.daily_appointment_limit = 1
        db.session.commit()
    user1 = login(client, "test1")
    user2 = login(client, "test2")
    payload = {"institution_id": institution_id, "package_id": package_id, "appointment_date": day.isoformat()}
    first = client.post("/api/appointments", headers=user1, json=payload)
    assert first.status_code == 201
    availability = client.get(f"/api/appointments/availability?appointment_date={day.isoformat()}", headers=user2).get_json()
    selected = next(item for item in availability["items"] if item["institution"]["id"] == institution_id)
    assert selected["remaining"] == 0 and selected["is_full"] is True
    full = client.post("/api/appointments", headers=user2, json=payload)
    assert full.status_code == 409 and full.get_json()["code"] == "APPOINTMENT_FULL"
    assert client.post(f"/api/appointments/{first.get_json()['item']['id']}/cancel", headers=user1).status_code == 200
    assert client.post("/api/appointments", headers=user2, json=payload).status_code == 201


def test_user_has_only_one_effective_appointment_per_day_across_institutions(app, client):
    first_institution, first_package = booking_fixture(app, 0)
    second_institution, second_package = booking_fixture(app, 1)
    headers = login(client, "test1")
    day = date.today() + timedelta(days=4)
    assert client.post("/api/appointments", headers=headers, json={"institution_id": first_institution, "package_id": first_package, "appointment_date": day.isoformat()}).status_code == 201
    duplicate = client.post("/api/appointments", headers=headers, json={"institution_id": second_institution, "package_id": second_package, "appointment_date": day.isoformat()})
    assert duplicate.status_code == 409


def test_institution_invalidation_is_final_and_visible_in_friend_timeline(app, client):
    institution_id, package_id = booking_fixture(app)
    owner = login(client, "test2")
    viewer = login(client, "test1")
    org = login(client, "institution1_staff1")
    day = date.today() + timedelta(days=5)
    created = client.post("/api/appointments", headers=owner, json={"institution_id": institution_id, "package_id": package_id, "appointment_date": day.isoformat()})
    appointment_id = created.get_json()["item"]["id"]
    with app.app_context():
        owner_id = User.query.filter_by(username="test2").first().id
    before_invalidation = client.get(f"/api/health/timeline?owner_id={owner_id}", headers=viewer).get_json()["items"]
    booked_event = next(item for item in before_invalidation if item["type"] == "appointment" and item["item"]["id"] == appointment_id)
    assert booked_event["item"]["status"] == "unfulfilled"
    assert booked_event["item"]["status_label"] == "未履约"
    assert booked_event["item"]["package_name"]

    invalidated = client.post(f"/api/org/appointments/{appointment_id}/invalidate", headers=org)
    assert invalidated.status_code == 200 and invalidated.get_json()["item"]["status"] == "invalidated"
    assert client.post(f"/api/org/appointments/{appointment_id}/attend", headers=org).status_code == 409
    assert client.post("/api/org/reports", headers=org, json={"appointment_id": appointment_id}).status_code == 409
    timeline = client.get(f"/api/health/timeline?owner_id={owner_id}", headers=viewer).get_json()["items"]
    event = next(item for item in timeline if item["type"] == "appointment" and item["item"]["id"] == appointment_id)
    assert event["item"]["appointment_date"] == day.isoformat()
    assert event["item"]["status"] == "invalidated"
    assert event["item"]["status_label"] == "已失效"
    assert "请重新预约或联系机构" in event["item"]["status_message"]


def test_package_changes_require_admin_review_and_support_withdrawal(app, client):
    org = login(client, "institution1_staff1")
    admin = login(client, "admin", "admin123")
    create_payload = {"name": "审核测试套餐", "focus_area": "预约审核联调", "gender_scope": "all", "price": 299, "description": "合成测试"}
    requested = client.post("/api/org/packages", headers=org, json=create_payload)
    assert requested.status_code == 201 and requested.get_json()["item"]["status"] == "pending"
    request_id = requested.get_json()["item"]["id"]
    with app.app_context():
        assert Package.query.filter_by(name="审核测试套餐").first() is None
    approved = client.post(f"/api/admin/package-change-requests/{request_id}/approve", headers=admin, json={})
    assert approved.status_code == 200, approved.get_json()
    package_id = approved.get_json()["item"]["package_id"]
    with app.app_context():
        assert float(db.session.get(Package, package_id).price) == 299

    changed = client.put(f"/api/org/packages/{package_id}", headers=org, json={"price": 399})
    assert changed.status_code == 202
    assert client.post(f"/api/admin/package-change-requests/{changed.get_json()['item']['id']}/reject", headers=admin, json={"review_note": "测试驳回"}).status_code == 200
    with app.app_context():
        assert float(db.session.get(Package, package_id).price) == 299

    deactivate = client.delete(f"/api/org/packages/{package_id}", headers=org)
    pending_id = deactivate.get_json()["item"]["id"]
    assert client.post(f"/api/org/package-change-requests/{pending_id}/withdraw", headers=org).status_code == 200
    with app.app_context():
        assert db.session.get(Package, package_id).is_active is True
        assert db.session.get(PackageChangeRequest, pending_id).status == "withdrawn"


def test_admin_direct_package_mutations_are_disabled(app, client):
    institution_id, package_id = booking_fixture(app)
    admin = login(client, "admin", "admin123")
    assert client.post(f"/api/admin/institutions/{institution_id}/packages", headers=admin, json={}).status_code == 405
    assert client.put(f"/api/admin/institutions/{institution_id}/packages/{package_id}", headers=admin, json={}).status_code == 405
    assert client.delete(f"/api/admin/institutions/{institution_id}/packages/{package_id}", headers=admin).status_code == 405
