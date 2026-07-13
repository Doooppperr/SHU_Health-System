def _auth_headers(client, username):
    client.post(
        "/api/auth/register",
        json=client.register_payload(username, email=f"{username}@example.com"),
    )
    login_response = client.post(
        "/api/auth/login",
        json=client.login_payload(username),
    )
    access_token = login_response.get_json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


def _first_institution_and_package(client, headers):
    institutions = client.get("/api/institutions", headers=headers).get_json()["items"]
    institution_id = institutions[0]["id"]
    packages = client.get(f"/api/institutions/{institution_id}/packages", headers=headers).get_json()["items"]
    package_id = packages[0]["id"]
    return institution_id, package_id


def _create_record(client, headers, exam_date):
    institution_id, package_id = _first_institution_and_package(client, headers)
    create_response = client.post(
        "/api/records",
        headers=headers,
        json={
            "exam_date": exam_date,
            "institution_id": institution_id,
            "package_id": package_id,
        },
    )
    assert create_response.status_code == 201
    return create_response.get_json()["item"]["id"]


def _add_indicator(client, headers, record_id, code, value):
    dict_response = client.get("/api/indicators/dicts", headers=headers)
    indicator = next(item for item in dict_response.get_json()["items"] if item["code"] == code)
    add_response = client.post(
        f"/api/records/{record_id}/indicators",
        headers=headers,
        json={"indicator_dict_id": indicator["id"], "value": value},
    )
    assert add_response.status_code == 201
    return indicator["id"]


def test_indicator_trend_for_owner(client):
    owner_headers = _auth_headers(client, "trend_owner")

    first_record_id = _create_record(client, owner_headers, "2026-01-10")
    second_record_id = _create_record(client, owner_headers, "2026-03-15")
    indicator_id = _add_indicator(client, owner_headers, first_record_id, "FBG", "6.4")
    _add_indicator(client, owner_headers, second_record_id, "FBG", "5.8")

    trend_response = client.get(f"/api/trends/indicators/{indicator_id}", headers=owner_headers)
    assert trend_response.status_code == 200
    payload = trend_response.get_json()

    assert payload["summary"]["count"] == 2
    assert payload["series"][0]["record_id"] == first_record_id
    assert payload["series"][1]["record_id"] == second_record_id
    assert payload["series"][0]["record_display_id"] == f"health{first_record_id}"
    assert payload["series"][1]["record_display_id"] == f"health{second_record_id}"
    assert payload["summary"]["latest"] == 5.8
    assert payload["summary"]["max"] == 6.4


def test_indicator_trend_requires_friend_authorization(client):
    owner_headers = _auth_headers(client, "trend_owner_b")
    manager_headers = _auth_headers(client, "trend_manager_b")

    owner_login = client.post(
        "/api/auth/login",
        json=client.login_payload("trend_owner_b"),
    )
    owner_id = owner_login.get_json()["user"]["id"]

    record_id = _create_record(client, owner_headers, "2026-02-12")
    indicator_id = _add_indicator(client, owner_headers, record_id, "FBG", "6.0")

    blocked_response = client.get(
        f"/api/trends/indicators/{indicator_id}?owner_id={owner_id}",
        headers=manager_headers,
    )
    assert blocked_response.status_code == 403

    add_relation = client.post(
        "/api/friends",
        headers=manager_headers,
        json={"friend_username": "trend_owner_b", "relation_name": "亲友"},
    )
    relation_id = add_relation.get_json()["item"]["id"]
    client.put(
        f"/api/friends/{relation_id}/authorization",
        headers=owner_headers,
        json={"auth_status": True},
    )

    allowed_response = client.get(
        f"/api/trends/indicators/{indicator_id}?owner_id={owner_id}",
        headers=manager_headers,
    )
    assert allowed_response.status_code == 200
    assert allowed_response.get_json()["summary"]["count"] == 1
