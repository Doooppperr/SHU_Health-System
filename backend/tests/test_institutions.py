def _auth_headers(client):
    client.post(
        "/api/auth/register",
        json=client.register_payload("inst_user", email="inst_user@example.com"),
    )
    login_response = client.post(
        "/api/auth/login",
        json=client.login_payload("inst_user"),
    )
    access_token = login_response.get_json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


def test_institution_list_requires_auth(client):
    response = client.get("/api/institutions")
    assert response.status_code == 401


def test_list_institutions_success(client):
    headers = _auth_headers(client)
    response = client.get("/api/institutions", headers=headers)

    assert response.status_code == 200
    payload = response.get_json()
    assert "items" in payload
    assert len(payload["items"]) >= 3


def test_institution_detail_and_packages(client):
    headers = _auth_headers(client)
    list_response = client.get("/api/institutions", headers=headers)
    institution_id = list_response.get_json()["items"][0]["id"]

    detail_response = client.get(f"/api/institutions/{institution_id}", headers=headers)
    assert detail_response.status_code == 200
    assert detail_response.get_json()["item"]["id"] == institution_id

    package_response = client.get(f"/api/institutions/{institution_id}/packages", headers=headers)
    assert package_response.status_code == 200
    assert len(package_response.get_json()["items"]) == 5


def test_institution_not_found(client):
    headers = _auth_headers(client)

    detail_response = client.get("/api/institutions/99999", headers=headers)
    assert detail_response.status_code == 404

    package_response = client.get("/api/institutions/99999/packages", headers=headers)
    assert package_response.status_code == 404
