from datetime import date, timedelta


PASSWORD = "Shuhealthdoc！"


def login(client, username):
    response = client.post("/api/auth/login", json=client.login_payload(username, PASSWORD))
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def test_timeline_unifies_exam_and_daily_personal_records(client):
    headers = login(client, "test1")

    response = client.get("/api/health/timeline?page=1&page_size=15", headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["pagination"]["page_size"] == 15
    assert {item["record_type"] for item in payload["items"]} >= {"exam", "self"}
    assert all(item["business_date"] and item["record_key"] for item in payload["items"])

    personal = client.get("/api/health/timeline?record_type=self", headers=headers)
    assert personal.status_code == 200
    personal_items = personal.get_json()["items"]
    assert personal_items and all(item["record_type"] == "self" for item in personal_items)
    assert all(item["summary"]["indicator_count"] >= 1 for item in personal_items)

    exams = client.get("/api/health/timeline?record_type=exam", headers=headers)
    assert exams.status_code == 200
    assert all(item["record_type"] == "exam" for item in exams.get_json()["items"])


def test_health_dashboard_is_task_oriented(client):
    headers = login(client, "test1")
    response = client.get("/api/health/dashboard", headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert {"today_measurements", "next_appointment", "latest_health_data",
            "active_waitlist", "recent_timeline"} <= set(payload)
    assert all(item["record_type"] in {"exam", "self"} for item in payload["recent_timeline"])
    if payload["next_appointment"]:
        assert payload["next_appointment"]["status"]["label"] == "预约成功"


def test_measurement_list_supports_recent_range_and_limit(client):
    headers = login(client, "test1")
    start = date.today() - timedelta(days=10)
    response = client.get(
        f"/api/self-measurements?start_date={start.isoformat()}&limit=3",
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["items"]) <= 3
    assert payload["pagination"]["page_size"] == 3


def test_booking_and_org_dashboard_payloads_are_display_ready(client):
    user_headers = login(client, "test1")
    groups = client.get("/api/booking-groups", headers=user_headers)
    assert groups.status_code == 200
    if groups.get_json()["items"]:
        group = groups.get_json()["items"][0]
        assert group["institution"]["name"]
        assert group["status_labels"]
        assert group["participant_names"]

    org_headers = login(client, "institution1_staff1")
    dashboard = client.get("/api/org/dashboard", headers=org_headers)
    assert dashboard.status_code == 200
    summary = dashboard.get_json()["summary"]
    assert {"today", "tasks", "recent_package_reviews"} <= set(summary)
    assert {"capacity", "booked", "remaining", "awaiting_arrival", "awaiting_archive"} <= set(summary["today"])


def test_health_trends_return_dates_and_explainable_reference_ranges(client):
    headers = login(client, "test1")
    domains = client.get("/api/health-domains", headers=headers).get_json()["items"]
    domain = next(item for item in domains if item["code"] == "basic")
    response = client.get(f"/api/health-trends/{domain['id']}", headers=headers)
    assert response.status_code == 200
    entries = response.get_json()["series_by_indicator"]
    assert entries
    for entry in entries:
        assert all(len(point["date"]) == 10 and point["date"][4] == "-" for point in entry["points"])
        assert {"kind", "label", "context", "varies"} <= set(entry["reference"])
