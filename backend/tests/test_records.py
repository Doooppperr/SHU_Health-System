from app.extensions import db
from app.models import User


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


def _create_record(client, headers):
    institution_id, package_id = _first_institution_and_package(client, headers)
    response = client.post(
        "/api/records",
        headers=headers,
        json={
            "exam_date": "2026-04-08",
            "institution_id": institution_id,
            "package_id": package_id,
        },
    )
    assert response.status_code == 201
    item = response.get_json()["item"]
    assert item["display_id"] == f"health{item['id']}"
    return item["id"]


def test_record_crud_flow(client):
    headers = _auth_headers(client, "record_user")

    record_id = _create_record(client, headers)

    list_response = client.get("/api/records", headers=headers)
    assert list_response.status_code == 200
    listed_item = next(
        item for item in list_response.get_json()["items"] if item["id"] == record_id
    )
    assert listed_item["display_id"] == f"health{record_id}"

    detail_response = client.get(f"/api/records/{record_id}", headers=headers)
    assert detail_response.status_code == 200
    detail_item = detail_response.get_json()["item"]
    assert detail_item["id"] == record_id
    assert detail_item["display_id"] == f"health{record_id}"

    delete_response = client.delete(f"/api/records/{record_id}", headers=headers)
    assert delete_response.status_code == 200

    detail_after_delete = client.get(f"/api/records/{record_id}", headers=headers)
    assert detail_after_delete.status_code == 404


def test_indicator_crud_and_deduplicate(client):
    headers = _auth_headers(client, "indicator_user")
    record_id = _create_record(client, headers)

    dict_response = client.get("/api/indicators/dicts", headers=headers)
    assert dict_response.status_code == 200
    fbg = next(item for item in dict_response.get_json()["items"] if item["code"] == "FBG")

    add_response = client.post(
        f"/api/records/{record_id}/indicators",
        headers=headers,
        json={"indicator_dict_id": fbg["id"], "value": "7.2"},
    )
    assert add_response.status_code == 201
    indicator_id = add_response.get_json()["item"]["id"]
    assert add_response.get_json()["item"]["is_abnormal"] is True

    duplicate_response = client.post(
        f"/api/records/{record_id}/indicators",
        headers=headers,
        json={"indicator_dict_id": fbg["id"], "value": "6.0"},
    )
    assert duplicate_response.status_code == 409

    update_response = client.put(
        f"/api/records/{record_id}/indicators/{indicator_id}",
        headers=headers,
        json={"value": "5.4"},
    )
    assert update_response.status_code == 200
    assert update_response.get_json()["item"]["is_abnormal"] is False

    delete_response = client.delete(
        f"/api/records/{record_id}/indicators/{indicator_id}",
        headers=headers,
    )
    assert delete_response.status_code == 200


def test_record_authorization_boundary(client):
    owner_headers = _auth_headers(client, "owner_user")
    outsider_headers = _auth_headers(client, "outsider_user")

    record_id = _create_record(client, owner_headers)
    fbg = next(item for item in client.get("/api/indicators/dicts", headers=owner_headers).get_json()["items"] if item["code"] == "FBG")

    add_response = client.post(
        f"/api/records/{record_id}/indicators",
        headers=owner_headers,
        json={"indicator_dict_id": fbg["id"], "value": "5.9"},
    )
    indicator_id = add_response.get_json()["item"]["id"]

    assert client.get(f"/api/records/{record_id}", headers=outsider_headers).status_code == 404
    assert client.delete(f"/api/records/{record_id}", headers=outsider_headers).status_code == 404
    assert (
        client.put(
            f"/api/records/{record_id}/indicators/{indicator_id}",
            headers=outsider_headers,
            json={"value": "4.9"},
        ).status_code
        == 404
    )


def test_friend_proxy_record_authorization_chain(client):
    manager_headers = _auth_headers(client, "proxy_manager")
    owner_headers = _auth_headers(client, "proxy_owner")

    owner_login = client.post(
        "/api/auth/login",
        json=client.login_payload("proxy_owner"),
    )
    owner_user_id = owner_login.get_json()["user"]["id"]

    institution_id, package_id = _first_institution_and_package(client, manager_headers)

    create_without_relation = client.post(
        "/api/records",
        headers=manager_headers,
        json={
            "owner_id": owner_user_id,
            "exam_date": "2026-04-08",
            "institution_id": institution_id,
            "package_id": package_id,
        },
    )
    assert create_without_relation.status_code == 403
    assert create_without_relation.get_json()["message"] == "friend relation not found"

    add_relation_response = client.post(
        "/api/friends",
        headers=manager_headers,
        json={"friend_username": "proxy_owner", "relation_name": "家人"},
    )
    assert add_relation_response.status_code == 201
    relation_id = add_relation_response.get_json()["item"]["id"]

    create_without_auth = client.post(
        "/api/records",
        headers=manager_headers,
        json={
            "owner_id": owner_user_id,
            "exam_date": "2026-04-08",
            "institution_id": institution_id,
            "package_id": package_id,
        },
    )
    assert create_without_auth.status_code == 403
    assert create_without_auth.get_json()["message"] == "friend authorization required"

    grant_response = client.put(
        f"/api/friends/{relation_id}/authorization",
        headers=owner_headers,
        json={"auth_status": True},
    )
    assert grant_response.status_code == 200
    assert grant_response.get_json()["item"]["auth_status"] is True

    create_with_auth = client.post(
        "/api/records",
        headers=manager_headers,
        json={
            "owner_id": owner_user_id,
            "exam_date": "2026-04-08",
            "institution_id": institution_id,
            "package_id": package_id,
        },
    )
    assert create_with_auth.status_code == 201
    record_id = create_with_auth.get_json()["item"]["id"]
    assert create_with_auth.get_json()["item"]["owner_id"] == owner_user_id

    detail_as_manager = client.get(f"/api/records/{record_id}", headers=manager_headers)
    assert detail_as_manager.status_code == 200

    manager_user_id = client.get("/api/users/me", headers=manager_headers).get_json()["user"]["id"]
    takeover = client.put(
        f"/api/records/{record_id}",
        headers=manager_headers,
        json={"owner_id": manager_user_id},
    )
    assert takeover.status_code == 403
    assert client.get(
        f"/api/records/{record_id}", headers=owner_headers
    ).get_json()["item"]["owner_id"] == owner_user_id

    revoke_response = client.put(
        f"/api/friends/{relation_id}/authorization",
        headers=owner_headers,
        json={"auth_status": False},
    )
    assert revoke_response.status_code == 200
    assert revoke_response.get_json()["item"]["auth_status"] is False

    detail_after_revoke = client.get(f"/api/records/{record_id}", headers=manager_headers)
    assert detail_after_revoke.status_code == 404


def test_admin_uses_dedicated_record_management_api(client, app):
    owner_headers = _auth_headers(client, "record_owner_admin_scope")
    admin_headers = _auth_headers(client, "record_admin_scope")

    with app.app_context():
        admin = User.query.filter_by(username="record_admin_scope").first()
        admin.role = "admin"
        db.session.commit()

    owner_login = client.post(
        "/api/auth/login",
        json=client.login_payload("record_owner_admin_scope"),
    )
    owner_user_id = owner_login.get_json()["user"]["id"]

    institution_id, package_id = _first_institution_and_package(client, admin_headers)

    create_by_admin = client.post(
        "/api/admin/records",
        headers=admin_headers,
        json={
            "owner_id": owner_user_id,
            "exam_date": "2026-04-08",
            "institution_id": institution_id,
            "package_id": package_id,
            "status": "confirmed",
        },
    )
    assert create_by_admin.status_code == 201
    record_id = create_by_admin.get_json()["item"]["id"]
    assert create_by_admin.get_json()["item"]["owner_id"] == owner_user_id
    assert create_by_admin.get_json()["item"]["display_id"] == f"health{record_id}"

    admin_list = client.get("/api/admin/records", headers=admin_headers)
    assert admin_list.status_code == 200
    listed_item = next(
        item for item in admin_list.get_json()["items"] if item["id"] == record_id
    )
    assert listed_item["display_id"] == f"health{record_id}"

    admin_detail = client.get(f"/api/admin/records/{record_id}", headers=admin_headers)
    assert admin_detail.status_code == 200
    assert admin_detail.get_json()["item"]["display_id"] == f"health{record_id}"

    dicts = client.get("/api/indicators/dicts", headers=admin_headers).get_json()["items"]
    fbg = next(item for item in dicts if item["code"] == "FBG")

    add_indicator = client.post(
        f"/api/admin/records/{record_id}/indicators",
        headers=admin_headers,
        json={"indicator_dict_id": fbg["id"], "value": "6.1"},
    )
    assert add_indicator.status_code == 201
    indicator_item = add_indicator.get_json()["item"]
    indicator_id = indicator_item["id"]
    assert indicator_item["record_display_id"] == f"health{record_id}"

    update_indicator = client.put(
        f"/api/admin/records/{record_id}/indicators/{indicator_id}",
        headers=admin_headers,
        json={"value": "5.8"},
    )
    assert update_indicator.status_code == 200

    delete_indicator = client.delete(
        f"/api/admin/records/{record_id}/indicators/{indicator_id}",
        headers=admin_headers,
    )
    assert delete_indicator.status_code == 200

    delete_record = client.delete(f"/api/admin/records/{record_id}", headers=admin_headers)
    assert delete_record.status_code == 200

    owner_detail_after_delete = client.get(f"/api/records/{record_id}", headers=owner_headers)
    assert owner_detail_after_delete.status_code == 404

    assert client.get("/api/records", headers=admin_headers).status_code == 403
