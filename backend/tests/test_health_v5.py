from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from app.extensions import db
from app.health.routes import as_calendar_date
from app.ai.rag import RetrievalResult
from app.models import HealthDomain, IndicatorDict, Institution, InstitutionReport, SelfMeasurement, User
from app.services.record_files import report_file_path


PASSWORD = "Shuhealthdoc！"


def login(client, username, password=PASSWORD):
    response = client.post("/api/auth/login", json=client.login_payload(username, password))
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def report_fixture(app, *, user="test3", institution_index=0):
    with app.app_context():
        person = User.query.filter_by(username=user).first()
        institution = Institution.query.order_by(Institution.id).all()[institution_index]
        indicator = IndicatorDict.query.filter_by(code="HR").first()
        package = next(item for item in institution.packages if item.is_active)
        return person.real_name, person.health_id, institution.id, package.id, indicator.id


def create_appointment(client, user_headers, org_headers, institution_id, package_id, exam_day):
    response = client.post("/api/appointments", headers=user_headers, json={
        "institution_id": institution_id,
        "package_id": package_id,
        "appointment_date": exam_day.isoformat(),
        "height_cm": 170,
        "weight_kg": 65,
    })
    assert response.status_code == 201
    appointment_id = response.get_json()["item"]["id"]
    assert client.post(f"/api/org/appointments/{appointment_id}/attend", headers=org_headers).status_code == 200
    return appointment_id


def create_locked_report(client, user_headers, org_headers, institution_id, package_id, indicator_id, exam_day):
    appointment_id = create_appointment(client, user_headers, org_headers, institution_id, package_id, exam_day)
    response = client.post("/api/org/reports", headers=org_headers, json={"appointment_id": appointment_id})
    assert response.status_code == 201
    report_id = response.get_json()["item"]["id"]
    indicator_response = client.post(
        f"/api/org/reports/{report_id}/indicators",
        headers=org_headers,
        json={"indicator_dict_id": indicator_id, "value": "73"},
    )
    assert indicator_response.status_code == 201
    domain_id = indicator_response.get_json()["item"]["display_domain_id"]
    missing = client.post(f"/api/org/reports/{report_id}/lock", headers=org_headers)
    assert missing.status_code == 409
    assert missing.get_json()["code"] == "MISSING_DOMAIN_CONCLUSIONS"
    conclusion = client.post(
        f"/api/org/health-data/{report_id}/text-results",
        headers=org_headers,
        json={
            "health_domain_id": domain_id,
            "title": "心血管检查结论",
            "body": "本次静息心率处于参考范围内，节律平稳。",
        },
    )
    assert conclusion.status_code == 201
    assert client.post(f"/api/org/reports/{report_id}/lock", headers=org_headers).status_code == 200
    return report_id


def test_health_identity_profile_and_multi_institution_accounts(app, client):
    captcha = client.get("/api/auth/captcha").get_json()
    missing_email = client.post("/api/auth/register", json={"username": "no-email", "password": "secret123", "captcha_id": captcha["captcha_id"], "captcha_answer": captcha["captcha_answer"]})
    assert missing_email.status_code == 400 and "邮箱" in missing_email.get_json()["message"]
    invalid_email = client.post("/api/auth/register", json={"username": "bad-email", "email": "not-an-email", "password": "secret123", "captcha_id": captcha["captcha_id"], "captcha_answer": captcha["captcha_answer"]})
    assert invalid_email.status_code == 400 and "有效" in invalid_email.get_json()["message"]
    registered = client.post("/api/auth/register", json={"username": "new-person", "email": "shared-registration@example.test", "password": "secret123", "captcha_id": captcha["captcha_id"], "captcha_answer": captcha["captcha_answer"]})
    assert registered.status_code == 201
    token = {"Authorization": f"Bearer {registered.get_json()['access_token']}"}
    health_id = registered.get_json()["user"]["health_id"]
    assert health_id.startswith("HID-")
    assert client.put("/api/profile/me", headers=token, json={"health_id": "HID-FORGED1"}).status_code == 409
    profile = client.put("/api/profile/me", headers=token, json={"real_name": "新用户", "birth_date": "1990-02-03", "gender": "female"})
    assert profile.status_code == 200 and profile.get_json()["item"]["health_id"] == health_id
    assert client.put("/api/profile/me", headers=token, json={"email": "first@example.test"}).status_code == 400
    bound = client.put("/api/auth/email", headers=token, json={"email": "first@example.test"})
    assert bound.status_code == 200 and bound.get_json()["user"]["email_verified_at"] is None
    with app.app_context():
        person = User.query.filter_by(username="new-person").first()
        person.email_verified_at = datetime.now(timezone.utc)
        db.session.commit()
    unchanged = client.put("/api/auth/email", headers=token, json={"email": "FIRST@example.test"})
    assert unchanged.status_code == 409
    phone_update = client.put("/api/profile/me", headers=token, json={"phone": "13800000000"})
    assert phone_update.status_code == 200 and phone_update.get_json()["item"]["email_verified_at"] is not None
    changed = client.put("/api/auth/email", headers=token, json={"email": "second@example.test"})
    assert changed.status_code == 200 and changed.get_json()["user"]["email_verified_at"] is None

    admin = login(client, "admin", "admin123")
    with app.app_context(): institution_id = Institution.query.first().id
    invite = client.post(f"/api/admin/institutions/{institution_id}/invite", headers=admin).get_json()["invite_code"]
    captcha = client.get("/api/auth/captcha").get_json()
    staff = client.post("/api/auth/register", json={"username": "third-staff", "email": "shared-registration@example.test", "password": "secret123", "invite_code": invite, "captcha_id": captcha["captcha_id"], "captcha_answer": captcha["captcha_answer"]})
    assert staff.status_code == 201 and staff.get_json()["user"]["role"] == "institution_admin"
    with app.app_context():
        institution = db.session.get(Institution, institution_id)
        assert staff.get_json()["user"]["email"] == institution.notification_email
        assert User.query.filter_by(managed_institution_id=institution_id, role="institution_admin").count() == 3


def test_institution_submission_auto_archives_to_registered_user(app, client):
    _name, _health_id, institution_id, package_id, indicator_id = report_fixture(app)
    user = login(client, "test3"); org = login(client, "institution1_staff1"); other_org = login(client, "institution2_staff1")
    first_day = date.today() + timedelta(days=7)
    report_id = create_locked_report(client, user, org, institution_id, package_id, indicator_id, first_day)
    assert client.put(f"/api/org/reports/{report_id}", headers=org, json={"exam_date": date.today().isoformat()}).status_code == 409
    submitted = client.post(f"/api/org/reports/{report_id}/submit", headers=org)
    assert submitted.status_code == 200 and submitted.get_json()["match_result"] == "matched"
    assert client.post(f"/api/org/reports/{report_id}/withdraw", headers=org).status_code == 404
    assert client.get(f"/api/org/reports/{report_id}", headers=other_org).status_code == 404
    assert any(item["id"] == report_id for item in client.get("/api/exam-reports", headers=user).get_json()["items"])
    assert client.get("/api/exam-registrations", headers=user).status_code == 404
    assert client.get("/api/records", headers=user).status_code == 404
    assert client.get("/api/admin/records", headers=login(client, "admin", "admin123")).status_code == 404


def test_reports_require_appointments_and_submit_rechecks_active_user(app, client):
    _name, health_id, institution_id, package_id, indicator_id = report_fixture(app)
    org = login(client, "institution1_staff1")
    user_headers = login(client, "test3")
    day = date.today() + timedelta(days=20)
    direct = client.post("/api/org/reports", headers=org, json={"subject_name": "不存在用户", "subject_health_id": "HID-UNKNOWN1", "exam_date": day.isoformat()})
    assert direct.status_code == 400 and "预约" in direct.get_json()["message"]

    locked_id = create_locked_report(client, user_headers, org, institution_id, package_id, indicator_id, day + timedelta(days=1))
    with app.app_context():
        user = User.query.filter_by(health_id=health_id).first()
        user.is_active = False
        db.session.commit()
    response = client.post(f"/api/org/reports/{locked_id}/submit", headers=org)
    assert response.status_code == 409
    assert "已注册普通用户" in response.get_json()["message"]
    with app.app_context():
        report = db.session.get(InstitutionReport, locked_id)
        assert report.status == "locked"
        assert report.matched_user_id is not None
        assert {item.status for item in InstitutionReport.query.all()} <= {"draft", "locked", "published"}


def test_self_measurement_trend_keeps_published_report_priority(app, client):
    assert as_calendar_date(datetime(2026, 7, 16, 8, 30)) == date(2026, 7, 16)
    assert as_calendar_date(date(2026, 7, 16)) == date(2026, 7, 16)
    headers = login(client, "test1")
    with app.app_context():
        weight = IndicatorDict.query.filter_by(code="WEIGHT").first(); bmi = IndicatorDict.query.filter_by(code="BMI").first()
        weight_id, bmi_id = weight.id, bmi.id
    day = date.today() - timedelta(days=4)
    for hour, value in ((8, 70.1), (20, 70.8)):
        response = client.post("/api/self-measurements", headers=headers, json={"indicator_dict_id": weight_id, "value": value, "measured_at": f"{day.isoformat()}T{hour:02d}:00:00+00:00"})
        assert response.status_code == 201
    assert client.post("/api/self-measurements", headers=headers, json={"indicator_dict_id": bmi_id, "value": 22, "measured_at": datetime.now(timezone.utc).isoformat()}).status_code == 400
    trend = client.get(f"/api/health/trends/{weight_id}", headers=headers).get_json()["points"]
    point = next(item for item in trend if item["date"] == day.isoformat())
    assert point["source"] == "institution_report" and point["value"] == 71.9


def test_friend_read_only_privacy_and_role_isolation(app, client):
    viewer = login(client, "test1")
    with app.app_context():
        owner = User.query.filter_by(username="test2").first()
        owner_id, owner_name = owner.id, owner.real_name
    timeline = client.get(f"/api/health/timeline?owner_id={owner_id}", headers=viewer)
    assert timeline.status_code == 200
    serialized = str(timeline.get_json())
    assert "health_id" not in serialized and "allergy_history" not in serialized and "subject_name_snapshot" not in serialized
    assert owner_name in serialized
    friends = client.get("/api/friends", headers=viewer).get_json()
    assert any(
        relation["friend_user"]["display_name"] == owner_name and relation["auth_status"]
        for relation in friends["outgoing"]
    )
    assert "manageable" not in friends
    with app.app_context():
        weight_id = IndicatorDict.query.filter_by(code="WEIGHT").first().id
        report_id = InstitutionReport.query.filter_by(
            matched_user_id=owner_id,
            status="published",
        ).first().id
    trend = client.get(
        f"/api/health/trends/{weight_id}?owner_id={owner_id}", headers=viewer
    )
    assert trend.status_code == 200
    assert trend.get_json()["owner"]["display_name"] == owner_name
    report = client.get(f"/api/exam-reports/{report_id}", headers=viewer)
    assert report.status_code == 200
    assert report.get_json()["owner"]["display_name"] == owner_name
    assert "subject_name_snapshot" not in report.get_json()["item"]
    assert client.get(
        f"/api/exam-reports/{report_id}", headers=login(client, "test3")
    ).status_code == 403
    assert client.post("/api/self-measurements", headers=login(client, "institution1_staff1"), json={}).status_code == 403
    assert client.get("/api/health/timeline", headers=login(client, "admin", "admin123")).status_code == 403


def test_ai_record_analysis_is_transparent_and_excludes_identity(app, client):
    headers = login(client, "test1")
    available = client.get("/api/ai/records", headers=headers)
    assert available.status_code == 200 and available.get_json()["items"]
    report_id = available.get_json()["items"][0]["id"]
    transparent = client.post(
        "/api/ai/analyze/stream",
        headers=headers,
        json={"selected_record_ids": [report_id]},
        buffered=True,
    )
    assert transparent.status_code == 200
    body = transparent.get_data(as_text=True)
    assert "event: done" in body

    # Old clients may continue sending the consent flag during a rolling
    # upgrade; it remains accepted but is no longer a blocking prerequisite.
    compatible = client.post(
        "/api/ai/analyze/stream",
        headers=headers,
        json={"selected_record_ids": [report_id], "consent": True},
        buffered=True,
    )
    assert compatible.status_code == 200
    with app.app_context():
        user = User.query.filter_by(username="test1").first()
        assert user.health_id not in body
        assert user.real_name not in body


def test_ai_trend_analysis_is_transparent_and_uses_server_data(app, client):
    headers = login(client, "test1")
    with app.app_context():
        domain_id = HealthDomain.query.filter_by(code="basic").one().id
    transparent = client.post(
        "/api/ai/trends/stream",
        headers=headers,
        json={"domain_id": domain_id},
        buffered=True,
    )
    assert transparent.status_code == 200
    body = transparent.get_data(as_text=True)
    assert '"mode":"trend_analysis"' in body
    assert 'event: done' in body


def test_org_ocr_mock_creates_reviewable_draft_and_lock_deletes_file(app, client):
    headers = login(client, "institution2_staff1")
    user_headers = login(client, "test3")
    _name, _health_id, institution_id, package_id, _indicator_id = report_fixture(app, institution_index=1)
    appointment_id = create_appointment(client, user_headers, headers, institution_id, package_id, date.today() + timedelta(days=25))
    response = client.post(
        "/api/org/reports/ocr", headers=headers,
        data={"file": (BytesIO(b"mock report"), "report.pdf"), "appointment_id": str(appointment_id)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    report_id = response.get_json()["item"]["id"]
    with app.app_context():
        report = db.session.get(InstitutionReport, report_id)
        path = report_file_path(report.temporary_file_url)
        assert path and path.exists() and report.indicators
        domain_ids = sorted({item.display_domain_id for item in report.indicators})
    for domain_id in domain_ids:
        added = client.post(
            f"/api/org/health-data/{report_id}/text-results",
            headers=headers,
            json={
                "health_domain_id": domain_id,
                "title": "检查结论",
                "body": "本次检查结果已完成复核。",
            },
        )
        assert added.status_code == 201
    locked = client.post(f"/api/org/reports/{report_id}/lock", headers=headers)
    assert locked.status_code == 200
    assert not path.exists()
    assert "raw_text" not in str(locked.get_json()["item"].get("ocr_diagnostics"))


def test_admin_cascade_deletes_regular_user_business_data(app, client):
    admin = login(client, "admin", "admin123")
    with app.app_context():
        user = User.query.filter_by(username="test3").first()
        indicator = IndicatorDict.query.filter_by(code="HR").first()
        user_id = user.id
        db.session.add(SelfMeasurement(user_id=user.id, indicator_dict_id=indicator.id, value=70, measured_at=datetime.now(timezone.utc)))
        db.session.commit()
    assert client.delete(f"/api/users/{user_id}", headers=admin, json={"confirm": True}).status_code == 200
    with app.app_context():
        assert db.session.get(User, user_id) is None
        assert SelfMeasurement.query.filter_by(user_id=user_id).count() == 0


def test_ai_health_question_uses_normal_answer_pipeline(app, client):
    class AvailableRetriever:
        @staticmethod
        def retrieve(*_args, **_kwargs):
            return RetrievalResult(status="empty")

    app.extensions["knowledge_retriever"] = AvailableRetriever()
    response = client.post(
        "/api/ai/chat/stream",
        headers=login(client, "test1"),
        json={"message": "我胸痛并且呼吸困难", "history": []},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '"decision":"answer"' in body
    assert '"stage":"retrieving"' in body


def test_ai_owner_scope_uses_published_reports_and_degrades_without_rag(app, client):
    class UnavailableRetriever:
        @staticmethod
        def retrieve(*_args, **_kwargs):
            return RetrievalResult(status="unavailable", error_code="test_unavailable")

    app.extensions["knowledge_retriever"] = UnavailableRetriever()
    with app.app_context():
        user = User.query.filter_by(username="test1").first()
        owner_id = user.id
        expected_ids = [
            item.id
            for item in InstitutionReport.query.filter_by(
                matched_user_id=user.id, status="published"
            ).order_by(InstitutionReport.exam_date, InstitutionReport.id)
        ]
    response = client.post(
        "/api/ai/chat",
        headers=login(client, "test1"),
        json={
            "message": "请解释这些历史报告的整体变化",
            "record_scope": {"owner_id": owner_id, "mode": "all_confirmed"},
            "consent": True,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["selected_record_ids"] == expected_ids
    assert payload["rag_used"] is False
    assert payload["retrieval_status"] == "unavailable"


def test_admin_ai_retrieves_only_public_audience(app, client):
    audiences = []

    class CapturingRetriever:
        @staticmethod
        def retrieve(_query, *, audience, **_kwargs):
            audiences.append(audience)
            return RetrievalResult(status="no_match")

    app.extensions["knowledge_retriever"] = CapturingRetriever()
    response = client.post(
        "/api/ai/chat",
        headers=login(client, "demo_admin"),
        json={"message": "请说明平台公共知识检索边界", "history": []},
    )
    assert response.status_code == 200
    assert audiences == ["public"]
