from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from app.extensions import db
from app.health.routes import as_calendar_date
from app.ai.rag import RetrievalResult
from app.models import IndicatorDict, Institution, InstitutionReport, SelfMeasurement, User
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
        return person.real_name, person.health_id, institution.id, indicator.id


def create_locked_report(client, headers, name, health_id, institution_id, indicator_id, exam_day):
    response = client.post("/api/org/reports", headers=headers, json={"subject_name": name, "subject_health_id": health_id, "exam_date": exam_day.isoformat()})
    assert response.status_code == 201
    report_id = response.get_json()["item"]["id"]
    assert client.post(f"/api/org/reports/{report_id}/indicators", headers=headers, json={"indicator_dict_id": indicator_id, "value": "73"}).status_code == 201
    assert client.post(f"/api/org/reports/{report_id}/lock", headers=headers).status_code == 200
    return report_id


def test_health_identity_profile_and_multi_institution_accounts(app, client):
    captcha = client.get("/api/auth/captcha").get_json()
    registered = client.post("/api/auth/register", json={"username": "new-person", "password": "secret123", "captcha_id": captcha["captcha_id"], "captcha_answer": captcha["captcha_answer"]})
    assert registered.status_code == 201
    token = {"Authorization": f"Bearer {registered.get_json()['access_token']}"}
    health_id = registered.get_json()["user"]["health_id"]
    assert health_id.startswith("HID-")
    assert client.put("/api/profile/me", headers=token, json={"health_id": "HID-FORGED1"}).status_code == 409
    profile = client.put("/api/profile/me", headers=token, json={"real_name": "新用户", "birth_date": "1990-02-03", "gender": "female"})
    assert profile.status_code == 200 and profile.get_json()["item"]["health_id"] == health_id

    admin = login(client, "admin", "admin123")
    with app.app_context(): institution_id = Institution.query.first().id
    invite = client.post(f"/api/admin/institutions/{institution_id}/invite", headers=admin).get_json()["invite_code"]
    captcha = client.get("/api/auth/captcha").get_json()
    staff = client.post("/api/auth/register", json={"username": "third-staff", "password": "secret123", "invite_code": invite, "captcha_id": captcha["captcha_id"], "captcha_answer": captcha["captcha_answer"]})
    assert staff.status_code == 201 and staff.get_json()["user"]["role"] == "institution_admin"
    with app.app_context(): assert User.query.filter_by(managed_institution_id=institution_id, role="institution_admin").count() == 3


def test_institution_submission_auto_archives_to_registered_user(app, client):
    name, health_id, institution_id, indicator_id = report_fixture(app)
    user = login(client, "test3"); org = login(client, "institution1_staff1"); other_org = login(client, "institution2_staff1")
    first_day = date.today() + timedelta(days=7)
    report_id = create_locked_report(client, org, name, health_id, institution_id, indicator_id, first_day)
    assert client.put(f"/api/org/reports/{report_id}", headers=org, json={"exam_date": date.today().isoformat()}).status_code == 409
    submitted = client.post(f"/api/org/reports/{report_id}/submit", headers=org)
    assert submitted.status_code == 200 and submitted.get_json()["match_result"] == "matched"
    assert client.post(f"/api/org/reports/{report_id}/withdraw", headers=org).status_code == 404
    assert client.get(f"/api/org/reports/{report_id}", headers=other_org).status_code == 404
    assert any(item["id"] == report_id for item in client.get("/api/exam-reports", headers=user).get_json()["items"])
    assert client.get("/api/exam-registrations", headers=user).status_code == 404
    assert client.get("/api/records", headers=user).status_code == 404
    assert client.get("/api/admin/records", headers=login(client, "admin", "admin123")).status_code == 404


def test_report_lock_rejects_unknown_identity_and_submit_rechecks_active_user(app, client):
    name, health_id, institution_id, indicator_id = report_fixture(app)
    org = login(client, "institution1_staff1")
    day = date.today() + timedelta(days=20)
    draft = client.post("/api/org/reports", headers=org, json={"subject_name": "不存在用户", "subject_health_id": "HID-UNKNOWN1", "exam_date": day.isoformat()})
    report_id = draft.get_json()["item"]["id"]
    assert client.post(f"/api/org/reports/{report_id}/indicators", headers=org, json={"indicator_dict_id": indicator_id, "value": "73"}).status_code == 201
    rejected_lock = client.post(f"/api/org/reports/{report_id}/lock", headers=org)
    assert rejected_lock.status_code == 409
    assert "registered user not found" in rejected_lock.get_json()["message"]

    locked_id = create_locked_report(client, org, name, health_id, institution_id, indicator_id, day + timedelta(days=1))
    with app.app_context():
        user = User.query.filter_by(health_id=health_id).first()
        user.is_active = False
        db.session.commit()
    response = client.post(f"/api/org/reports/{locked_id}/submit", headers=org)
    assert response.status_code == 409
    assert "registered user not found" in response.get_json()["message"]
    with app.app_context():
        report = db.session.get(InstitutionReport, locked_id)
        assert report.status == "locked"
        assert report.matched_user_id is None
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
    assert owner_name not in serialized
    friends = client.get("/api/friends", headers=viewer).get_json()
    assert any(
        relation["friend_user"]["username"] == "test2" and relation["auth_status"]
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
    assert trend.get_json()["owner"]["username"] == "test2"
    report = client.get(f"/api/exam-reports/{report_id}", headers=viewer)
    assert report.status_code == 200
    assert report.get_json()["owner"]["username"] == "test2"
    assert "subject_name_snapshot" not in report.get_json()["item"]
    assert client.get(
        f"/api/exam-reports/{report_id}", headers=login(client, "test3")
    ).status_code == 403
    assert client.post("/api/self-measurements", headers=login(client, "institution1_staff1"), json={}).status_code == 403
    assert client.get("/api/health/timeline", headers=login(client, "admin", "admin123")).status_code == 403


def test_ai_requires_per_request_consent_and_excludes_identity(app, client):
    headers = login(client, "test1")
    available = client.get("/api/ai/records", headers=headers)
    assert available.status_code == 200 and available.get_json()["items"]
    report_id = available.get_json()["items"][0]["id"]
    denied = client.post("/api/ai/analyze/stream", headers=headers, json={"selected_record_ids": [report_id]})
    assert denied.status_code == 400
    allowed = client.post("/api/ai/analyze/stream", headers=headers, json={"selected_record_ids": [report_id], "consent": True}, buffered=True)
    assert allowed.status_code == 200
    body = allowed.get_data(as_text=True)
    with app.app_context():
        user = User.query.filter_by(username="test1").first()
        assert user.health_id not in body
        assert user.real_name not in body


def test_org_ocr_mock_creates_reviewable_draft_and_lock_deletes_file(app, client):
    headers = login(client, "institution2_staff1")
    name, health_id, _institution_id, _indicator_id = report_fixture(app, institution_index=1)
    response = client.post(
        "/api/org/reports/ocr", headers=headers,
        data={"file": (BytesIO(b"mock report"), "report.pdf"), "subject_name": name, "subject_health_id": health_id, "exam_date": (date.today() + timedelta(days=40)).isoformat()},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    report_id = response.get_json()["item"]["id"]
    with app.app_context():
        report = db.session.get(InstitutionReport, report_id)
        path = report_file_path(report.temporary_file_url)
        assert path and path.exists() and report.indicators
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


def test_ai_emergency_skips_public_knowledge_retrieval(app, client):
    class ForbiddenRetriever:
        @staticmethod
        def retrieve(*_args, **_kwargs):
            raise AssertionError("emergency path must not invoke RAG")

    app.extensions["knowledge_retriever"] = ForbiddenRetriever()
    response = client.post(
        "/api/ai/chat/stream",
        headers=login(client, "test1"),
        json={"message": "我胸痛并且呼吸困难", "history": []},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '"decision":"emergency"' in body
    assert '"stage":"retrieving"' not in body


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
