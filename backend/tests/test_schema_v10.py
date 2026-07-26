from datetime import date, timedelta
from io import BytesIO

from PIL import Image

from app.extensions import db
from app.models import (
    Appointment,
    FriendRelation,
    Institution,
    NotificationOutbox,
    ReportAssetType,
    User,
    UserNotification,
)


PASSWORD = "Shuhealthdoc！"


def login(client, username, password=PASSWORD):
    response = client.post("/api/auth/login", json=client.login_payload(username, password))
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def first_booking_target(app):
    with app.app_context():
        institution = Institution.query.order_by(Institution.id).first()
        package = next(row for row in institution.packages if row.is_active)
        return institution.id, package.id


def test_friend_binding_uses_health_id_and_displays_real_name(app, client):
    headers = login(client, "test3")
    with app.app_context():
        target = User.query.filter_by(username="test4").one()
        target_id, health_id, real_name = target.id, target.health_id, target.real_name
    rejected = client.post("/api/friends", headers=headers, json={"friend_username": "test4"})
    assert rejected.status_code == 400
    created = client.post("/api/friends", headers=headers, json={
        "health_id": health_id,
        "relation_name": "家人",
    })
    assert created.status_code == 201
    pending = created.get_json()["item"]
    assert "username" not in pending["friend_user"]
    assert pending["friend_user"]["display_name"] != real_name
    target_headers = login(client, "test4")
    accepted = client.put(
        f"/api/friends/{pending['id']}/authorization",
        headers=target_headers,
        json={"auth_status": True},
    )
    assert accepted.status_code == 200
    outgoing = client.get("/api/friends", headers=headers).get_json()["outgoing"]
    relation = next(row for row in outgoing if row["id"] == pending["id"])
    assert relation["friend_user"] == {"id": target_id, "display_name": real_name}


def test_booking_intakes_create_private_institution_snapshot(app, client):
    headers = login(client, "test1")
    institution_id, package_id = first_booking_target(app)
    with app.app_context():
        booker = User.query.filter_by(username="test1").one()
        friend = User.query.filter_by(username="test2").one()
        relation = FriendRelation.query.filter_by(user_id=booker.id, friend_user_id=friend.id).one()
        relation.booking_auth_status = True
        db.session.commit()
        participant_ids = [booker.id, friend.id]
    response = client.post("/api/booking-groups", headers=headers, json={
        "institution_id": institution_id,
        "package_id": package_id,
        "appointment_date": (date.today() + timedelta(days=23)).isoformat(),
        "participant_user_ids": participant_ids,
        "participant_intakes": [
            {"user_id": participant_ids[0], "height_cm": 176, "weight_kg": 72},
            {"user_id": participant_ids[1], "height_cm": 163, "weight_kg": 59},
        ],
        "notice_confirmed": True,
    })
    assert response.status_code == 201, response.get_json()
    with app.app_context():
        appointments = Appointment.query.filter_by(
            booking_group_id=response.get_json()["item"]["id"]
        ).order_by(Appointment.user_id).all()
        assert all(row.intake_captured_at for row in appointments)
        assert all(row.bmi_snapshot for row in appointments)
        proxy = next(row for row in appointments if row.user_id == participant_ids[1])
        assert proxy.allergy_history_snapshot == db.session.get(User, participant_ids[1]).allergy_history
        assert float(proxy.height_cm_snapshot) == 163


def test_institution_cancellation_notifies_user_with_sibling_snapshot(app, client):
    user_headers = login(client, "test1")
    institution_id, package_id = first_booking_target(app)
    with app.app_context():
        user_id = User.query.filter_by(username="test1").one().id
    booked = client.post("/api/booking-groups", headers=user_headers, json={
        "institution_id": institution_id,
        "package_id": package_id,
        "appointment_date": (date.today() + timedelta(days=24)).isoformat(),
        "participant_user_ids": [user_id],
        "participant_intakes": [{"user_id": user_id, "height_cm": 176, "weight_kg": 72}],
        "notice_confirmed": True,
    })
    appointment_id = booked.get_json()["item"]["appointments"][0]["id"]
    org_headers = login(client, "institution1_staff1")
    closed = client.post(f"/api/org/appointments/{appointment_id}/close", headers=org_headers, json={
        "reason_type": "institution_cancelled",
        "reason_code": "equipment_failure",
        "reason_text": "心电设备突发故障，今日无法完成检查",
    })
    assert closed.status_code == 200
    assert closed.get_json()["item"]["status"] == "institution_cancelled"
    with app.app_context():
        notice = UserNotification.query.filter_by(
            user_id=user_id,
            event_type="appointment_institution_cancelled",
        ).order_by(UserNotification.id.desc()).first()
        assert notice and notice.payload["alternatives"]
        assert NotificationOutbox.query.filter_by(
            event_type="appointment_institution_cancelled"
        ).first()


def test_admin_user_pagination_detail_and_permanent_password_change(app, client):
    admin = login(client, "admin", "admin123")
    listing = client.get("/api/users?page=1&page_size=20&role=user&q=test", headers=admin)
    assert listing.status_code == 200
    assert listing.get_json()["pagination"]["page_size"] == 20
    target = listing.get_json()["items"][0]
    detail = client.get(f"/api/users/{target['id']}", headers=admin).get_json()["item"]
    assert {"real_name", "health_id", "email", "phone", "created_at"} <= set(detail)
    changed = client.post(
        f"/api/users/{target['id']}/password",
        headers=admin,
        json={"password": "NewDemoPass123!"},
    )
    assert changed.status_code == 200
    assert changed.get_json()["delivery"]["status"] == "pending"
    assert login(client, target["username"], "NewDemoPass123!")


def test_ai_institution_recommendation_is_grounded_in_platform_data(app, client):
    headers = login(client, "test1")
    response = client.post("/api/ai/chat/stream", headers=headers, json={
        "message": "我住在徐汇区，请推荐平台里的体检机构",
        "history": [],
    }, buffered=True)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "系统机构数据" in body
    assert "徐汇" in body
    assert "system_data" in body


def test_structured_asset_slot_rejects_duplicate_upload(app, client):
    user_headers = login(client, "test1")
    institution_id, package_id = first_booking_target(app)
    appointment = client.post("/api/appointments", headers=user_headers, json={
        "institution_id": institution_id,
        "package_id": package_id,
        "appointment_date": (date.today() + timedelta(days=25)).isoformat(),
        "height_cm": 170,
        "weight_kg": 65,
    }).get_json()["item"]
    org_headers = login(client, "institution1_staff1")
    assert client.post(f"/api/org/appointments/{appointment['id']}/attend", headers=org_headers).status_code == 200
    report = client.post("/api/org/reports", headers=org_headers, json={"appointment_id": appointment["id"]}).get_json()["item"]
    available = client.get(f"/api/org/report-asset-types?report_id={report['id']}", headers=org_headers).get_json()["items"]
    assert available
    slot = next((item for item in available if item["max_files"] == 1), available[0])
    image = Image.new("RGB", (20, 20), "white")
    raw = BytesIO()
    image.save(raw, format="PNG")
    raw_bytes = raw.getvalue()

    def upload():
        return client.post(
            f"/api/org/health-data/{report['id']}/assets",
            headers=org_headers,
            data={
                "file": (BytesIO(raw_bytes), "synthetic.png"),
                "health_domain_id": str(slot["domain_id"]),
                "asset_type_id": str(slot["id"]),
                "title": slot["name"],
                "annotation": "合成附件，非诊断依据",
            },
            content_type="multipart/form-data",
        )

    assert upload().status_code == 201
    assert upload().status_code == 409
