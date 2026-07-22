from datetime import date, timedelta

from app.extensions import db
from app.models import (
    Appointment, FriendRelation, HealthDomain, IndicatorDict, Institution,
    InstitutionReport, NotificationOutbox, Package, ReportAccessLog, User,
    WaitlistSubscription,
)
from app.health_data_v7.routes import _numeric_reference


PASSWORD = "Shuhealthdoc！"


def test_report_reference_parser_keeps_institution_one_sided_ranges():
    assert _numeric_reference("< 5.2 mmol/L") == (None, 5.2)
    assert _numeric_reference("不低于 1.0") == (1.0, None)
    assert _numeric_reference("60-100 次/分") == (60.0, 100.0)
    assert _numeric_reference("60–100 次/分") == (60.0, 100.0)


def login(client, username):
    response = client.post("/api/auth/login", json=client.login_payload(username, PASSWORD))
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def package_for(institution, name_part=None):
    rows = [row for row in institution.packages if row.is_active]
    return next((row for row in rows if name_part and name_part in row.name), rows[0])


def test_health_data_read_models_are_domain_based_and_paginated(app, client):
    headers = login(client, "test1")
    domains = client.get("/api/health-domains", headers=headers)
    assert domains.status_code == 200 and len(domains.get_json()["items"]) == 8
    listing = client.get("/api/health-data?page=1&page_size=15", headers=headers)
    assert listing.status_code == 200
    payload = listing.get_json()
    assert payload["pagination"]["page_size"] == 15
    assert {item["source_type"] for item in payload["items"]} == {"institution"}
    detail = client.get(f"/api/health-data/{payload['items'][0]['health_data_id']}", headers=headers)
    assert detail.status_code == 200 and "sections" in detail.get_json()["item"]
    domain_id = domains.get_json()["items"][0]["id"]
    trend = client.get(f"/api/health-trends/{domain_id}", headers=headers)
    assert trend.status_code == 200 and "series_by_indicator" in trend.get_json()


def test_personal_trends_are_not_hidden_when_the_domain_has_institution_reports(app, client):
    headers = login(client, "test1")
    with app.app_context():
        domain_id = HealthDomain.query.filter_by(code="basic").one().id

    response = client.get(
        f"/api/health-trends/{domain_id}?source_type=self",
        headers=headers,
    )

    assert response.status_code == 200
    series = response.get_json()["series_by_indicator"]
    weight = next(item for item in series if item["indicator"]["code"] == "WEIGHT")
    assert len(weight["points"]) >= 2
    assert {point["source"]["type"] for point in weight["points"]} == {"self"}


def test_trend_reference_prefers_report_and_falls_back_to_public_guideline(app, client):
    headers = login(client, "test1")
    with app.app_context():
        cardio_id = HealthDomain.query.filter_by(code="cardio").one().id

    report_response = client.get(
        f"/api/health-trends/{cardio_id}?source_type=institution",
        headers=headers,
    )
    assert report_response.status_code == 200
    report_series = report_response.get_json()["series_by_indicator"]
    heart_rate = next(item for item in report_series if item["indicator"]["code"] == "HR")
    assert heart_rate["reference"]["kind"] == "report"
    assert heart_rate["reference"]["low"] is not None
    assert heart_rate["reference"]["high"] is not None

    self_response = client.get(
        f"/api/health-trends/{cardio_id}?source_type=self",
        headers=headers,
    )
    self_series = self_response.get_json()["series_by_indicator"]
    self_heart_rate = next(item for item in self_series if item["indicator"]["code"] == "HR")
    assert self_heart_rate["reference"]["kind"] == "guideline"
    assert self_heart_rate["reference"]["source_url"].startswith("https://")


def test_same_organization_published_reports_are_read_only_and_audited(app, client):
    sibling = login(client, "institution4_staff1")
    other_organization = login(client, "institution2_staff1")
    with app.app_context():
        source = db.session.get(Institution, 1)
        sibling_branch = db.session.get(Institution, 4)
        assert source.organization_id == sibling_branch.organization_id
        report = InstitutionReport.query.filter_by(institution_id=source.id, status="published").first()
        report_id = report.id
    listing = client.get("/api/org/reports?scope=organization", headers=sibling)
    assert listing.status_code == 200
    shared = next(item for item in listing.get_json()["items"] if item["id"] == report_id)
    assert shared["access_mode"] == "cross_branch_read_only" and shared["can_edit"] is False
    detail = client.get(f"/api/org/reports/{report_id}", headers=sibling)
    assert detail.status_code == 200
    assert detail.get_json()["item"]["access_mode"] == "cross_branch_read_only"
    assert client.put(f"/api/org/reports/{report_id}", headers=sibling, json={"subject_name": "禁止修改"}).status_code == 404
    assert client.get(f"/api/org/reports/{report_id}", headers=other_organization).status_code == 404
    with app.app_context():
        assert ReportAccessLog.query.filter_by(report_id=report_id, access_type="detail").count() >= 1


def test_booking_group_is_atomic_and_proxy_booking_is_separate(app, client):
    booker = login(client, "test1")
    with app.app_context():
        user1 = User.query.filter_by(username="test1").first()
        user2 = User.query.filter_by(username="test2").first()
        relation = FriendRelation.query.filter_by(user_id=user1.id, friend_user_id=user2.id).first()
        relation.booking_auth_status = True
        relation.booking_authorized_at = relation.created_at
        institution = Institution.query.order_by(Institution.id).first()
        institution.daily_appointment_limit = 2
        package = package_for(institution)
        ids = (user1.id, user2.id, institution.id, package.id)
        db.session.commit()
    day = date.today() + timedelta(days=6)
    response = client.post("/api/booking-groups", headers=booker, json={
        "institution_id": ids[2], "package_id": ids[3], "appointment_date": day.isoformat(),
        "participant_user_ids": [ids[0], ids[1]], "notice_confirmed": True,
    })
    assert response.status_code == 201, response.get_json()
    item = response.get_json()["item"]
    assert item["party_size"] == 2 and len(item["appointments"]) == 2
    with app.app_context():
        rows = Appointment.query.filter_by(booking_group_id=item["id"]).all()
        assert len(rows) == 2 and {row.user_id for row in rows} == {ids[0], ids[1]}
        user_mail = NotificationOutbox.query.filter_by(event_type="booking_user_confirmed").all()
        assert len(user_mail) == 2
        assert len({row.idempotency_key for row in user_mail}) == 2
    duplicate = client.post("/api/booking-groups", headers=booker, json={
        "institution_id": ids[2], "package_id": ids[3], "appointment_date": day.isoformat(),
        "participant_user_ids": [ids[0]], "notice_confirmed": True,
    })
    assert duplicate.status_code == 409
    assert duplicate.get_json()["code"] == "APPOINTMENT_DATE_CONFLICT"
    assert duplicate.get_json()["appointment_date"] == day.isoformat()
    # Proxy booking does not imply permission to read the other subject's health data.
    with app.app_context():
        relation = FriendRelation.query.filter_by(user_id=ids[0], friend_user_id=ids[1]).first()
        relation.auth_status = False; db.session.commit()
    assert client.get(f"/api/health-data?owner_id={ids[1]}", headers=booker).status_code == 403


def test_waitlist_only_notifies_after_capacity_crosses_threshold(app, client):
    filler = login(client, "test2"); subscriber = login(client, "test1")
    with app.app_context():
        existing_outbox_ids = {
            row.id for row in NotificationOutbox.query.filter_by(
                event_type="waitlist_available"
            ).all()
        }
        institution = Institution.query.order_by(Institution.id).first()
        institution.daily_appointment_limit = 1
        package = package_for(institution)
        institution_id, package_id = institution.id, package.id
        db.session.commit()
    day = date.today() + timedelta(days=7)
    filled = client.post("/api/booking-groups", headers=filler, json={
        "institution_id": institution_id, "package_id": package_id,
        "appointment_date": day.isoformat(), "notice_confirmed": True,
    })
    assert filled.status_code == 201
    subscription = client.post("/api/waitlist-subscriptions", headers=subscriber, json={
        "institution_id": institution_id, "package_id": package_id,
        "appointment_date": day.isoformat(), "participant_user_ids": [],
    })
    assert subscription.status_code == 400  # empty groups are never valid
    subscription = client.post("/api/waitlist-subscriptions", headers=subscriber, json={
        "institution_id": institution_id, "package_id": package_id,
        "appointment_date": day.isoformat(), "notice_confirmed": True,
    })
    assert subscription.status_code == 201, subscription.get_json()
    group_id = filled.get_json()["item"]["id"]
    assert client.post(f"/api/booking-groups/{group_id}/cancel", headers=filler).status_code == 200
    with app.app_context():
        row = db.session.get(WaitlistSubscription, subscription.get_json()["item"]["id"])
        assert row.status == "active"
        outbox = NotificationOutbox.query.filter(
            NotificationOutbox.event_type == "waitlist_available",
            NotificationOutbox.id.notin_(existing_outbox_ids),
        ).all()
        assert len(outbox) == 1 and "不代表预约成功" in outbox[0].payload["message"]


def test_report_indicator_outside_package_domain_is_blocked(app, client):
    user = login(client, "test3"); org = login(client, "institution1_staff1")
    with app.app_context():
        institution = Institution.query.order_by(Institution.id).first()
        package = package_for(institution, "心脑血管")
        alt = IndicatorDict.query.filter_by(code="ALT").first()
        institution_id, package_id, alt_id = institution.id, package.id, alt.id
    day = date.today() + timedelta(days=8)
    appointment = client.post("/api/appointments", headers=user, json={
        "institution_id": institution_id, "package_id": package_id, "appointment_date": day.isoformat()})
    assert appointment.status_code == 201
    appointment_id = appointment.get_json()["item"]["id"]
    assert client.post(f"/api/org/appointments/{appointment_id}/attend", headers=org).status_code == 200
    report = client.post("/api/org/reports", headers=org, json={"appointment_id": appointment_id})
    assert report.status_code == 201
    rejected = client.post(f"/api/org/reports/{report.get_json()['item']['id']}/indicators",
                           headers=org, json={"indicator_dict_id": alt_id, "value": "20"})
    assert rejected.status_code == 400 and rejected.get_json()["code"] == "DOMAIN_NOT_ALLOWED"
