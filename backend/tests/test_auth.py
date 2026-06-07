def test_me_requires_auth(client):
    response = client.get("/api/users/me")
    assert response.status_code == 401


def test_register_login_and_get_me(client):
    register_payload = client.register_payload("alice", "secret123", email="alice@example.com")
    register_response = client.post("/api/auth/register", json=register_payload)
    assert register_response.status_code == 201
    register_tokens = register_response.get_json()
    assert "access_token" in register_tokens
    assert "refresh_token" in register_tokens

    login_response = client.post(
        "/api/auth/login",
        json=client.login_payload("alice", "secret123"),
    )
    assert login_response.status_code == 200
    tokens = login_response.get_json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me_response = client.get("/api/users/me", headers=headers)
    assert me_response.status_code == 200
    data = me_response.get_json()
    assert data["user"]["username"] == "alice"


def test_login_with_invalid_password_returns_401(client):
    client.post(
        "/api/auth/register",
        json=client.register_payload("bob", "secret123", email="bob@example.com"),
    )

    response = client.post(
        "/api/auth/login",
        json=client.login_payload("bob", "wrong-password"),
    )
    assert response.status_code == 401


def test_register_requires_captcha(client):
    response = client.post(
        "/api/auth/register",
        json={"username": "register_required", "password": "secret123"},
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "username, password and captcha are required"


def test_register_rejects_invalid_captcha(client):
    payload = client.register_payload("register_invalid", "secret123")
    payload["captcha_answer"] = "WRONG"

    response = client.post("/api/auth/register", json=payload)

    assert response.status_code == 400
    assert response.get_json()["message"] == "invalid captcha"


def test_login_requires_captcha(client):
    client.post(
        "/api/auth/register",
        json=client.register_payload("captcha_required"),
    )

    response = client.post(
        "/api/auth/login",
        json={"username": "captcha_required", "password": "secret123"},
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "username, password and captcha are required"


def test_login_rejects_invalid_captcha(client):
    client.post(
        "/api/auth/register",
        json=client.register_payload("captcha_invalid"),
    )
    payload = client.login_payload("captcha_invalid", "secret123")
    payload["captcha_answer"] = "WRONG"

    response = client.post("/api/auth/login", json=payload)

    assert response.status_code == 400
    assert response.get_json()["message"] == "invalid captcha"


def test_captcha_can_only_be_used_once(client):
    client.post(
        "/api/auth/register",
        json=client.register_payload("captcha_once"),
    )
    payload = client.login_payload("captcha_once", "secret123")

    first_response = client.post("/api/auth/login", json=payload)
    second_response = client.post("/api/auth/login", json=payload)

    assert first_response.status_code == 200
    assert second_response.status_code == 400
    assert second_response.get_json()["message"] == "invalid captcha"


def test_refresh_token_flow(client):
    client.post(
        "/api/auth/register",
        json=client.register_payload("carol", "secret123", email="carol@example.com"),
    )

    login_response = client.post(
        "/api/auth/login",
        json=client.login_payload("carol", "secret123"),
    )
    refresh_token = login_response.get_json()["refresh_token"]

    refresh_response = client.post(
        "/api/auth/refresh",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )

    assert refresh_response.status_code == 200
    assert "access_token" in refresh_response.get_json()
