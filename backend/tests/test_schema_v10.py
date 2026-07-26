from datetime import date, timedelta
from io import BytesIO

from PIL import Image

from app.demo_indicator_values import DEMO_REALISTIC_SERIES, demo_realistic_status
from app.extensions import db
from app.models import (
    Appointment,
    FriendRelation,
    IndicatorDict,
    Institution,
    InstitutionReport,
    NotificationOutbox,
    ReportIndicator,
    ReportAssetType,
    User,
    UserNotification,
)
from app.services.indicator_values import (
    IndicatorValueError,
    evaluate_result_status,
    parse_reference_bounds,
    result_status_is_displayable,
    validate_indicator_plausibility,
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


def test_booking_intake_defaults_do_not_block_appointment_initialization(app, client):
    headers = login(client, "test1")
    response = client.get("/api/booking-intake-defaults", headers=headers)
    assert response.status_code == 200, response.get_json()
    assert set(response.get_json()["item"]).issubset({"height_cm", "weight_kg"})


def test_descriptive_body_measurements_are_not_labelled_normal_or_abnormal(app):
    with app.app_context():
        height = IndicatorDict.query.filter_by(code="HEIGHT").one()
        weight = IndicatorDict.query.filter_by(code="WEIGHT").one()
        assert evaluate_result_status(height, "11.7", abnormal_flag="normal") == "unknown"
        assert evaluate_result_status(weight, "68", reference_text="50—80 kg") == "unknown"
        assert result_status_is_displayable("unknown") is False
        for definition, value in ((height, "11.7"), (weight, "12.7")):
            try:
                validate_indicator_plausibility(definition, value)
            except IndicatorValueError:
                pass
            else:
                raise AssertionError("implausible adult body measurement was accepted")


def test_full_scale_demo_vitals_stay_within_plausible_measurement_limits():
    bounds = {
        "HEIGHT": (80, 250),
        "WEIGHT": (20, 500),
        "WAIST": (40, 180),
        "HIP": (50, 200),
        "WHR": (0.4, 1.5),
        "TEMP": (30, 43),
        "SPO2": (50, 100),
        "FVC": (0.5, 8),
        "FEV1": (0.5, 7),
        "FEV1_FVC": (20, 100),
    }
    for code, (low, high) in bounds.items():
        values = [float(value) for value in DEMO_REALISTIC_SERIES[code]]
        assert all(low <= value <= high for value in values), code
    assert demo_realistic_status("HEIGHT", "170") == "unknown"
    assert demo_realistic_status("TEMP", "35.8") == "low"
    assert demo_realistic_status("TEMP", "36.6") == "normal"
    assert demo_realistic_status("TEMP", "38.5") == "high"


def test_four_year_demo_story_is_dense_coherent_and_non_diagnostic():
    assert all(len(values) == 16 for values in DEMO_REALISTIC_SERIES.values())
    weights = [float(value) for value in DEMO_REALISTIC_SERIES["WEIGHT"]]
    ldl = [float(value) for value in DEMO_REALISTIC_SERIES["LDL"]]
    alt = [float(value) for value in DEMO_REALISTIC_SERIES["ALT"]]
    assert weights[0] > weights[9]
    assert weights[11] > weights[9]
    assert weights[-1] < weights[11]
    assert ldl[0] > ldl[9]
    assert ldl[11] > ldl[9]
    assert ldl[-1] < ldl[11]
    assert alt[0] > alt[9]
    assert alt[11] > alt[9]
    assert alt[-1] < alt[11]
    assert len(set(DEMO_REALISTIC_SERIES["HEIGHT"])) == 1


def test_report_range_precedes_catalog_and_unknown_status_is_hidden(app):
    with app.app_context():
        heart_rate = IndicatorDict.query.filter_by(code="HR").one()
        assert parse_reference_bounds("参考范围 50-70 次/分") == (50, 70)
        assert evaluate_result_status(
            heart_rate,
            "75",
            reference_text="参考范围 50-70 次/分",
        ) == "high"

        height = IndicatorDict.query.filter_by(code="HEIGHT").one()
        report = InstitutionReport.query.filter_by(status="published").first()
        row = ReportIndicator(
            report=report,
            indicator_dict=height,
            value="11.7",
            result_status="normal",
            is_abnormal=False,
            display_domain_id=height.domain_links[0].health_domain_id,
            abnormal_flag="normal",
        )
        with db.session.no_autoflush:
            payload = row.to_dict()
        assert payload["result_status"] == "unknown"
        assert payload["status_displayable"] is False
        assert payload["is_abnormal"] is False


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
