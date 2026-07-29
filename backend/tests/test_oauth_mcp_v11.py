import base64
import hashlib
from urllib.parse import parse_qs, urlparse


def _login(client, username, password):
    response = client.post(
        "/api/auth/login", json=client.login_payload(username, password)
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def _pkce(verifier):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )


def test_oauth_pkce_rotation_replay_and_internal_verification(client):
    registered = client.post(
        "/oauth/register",
        json={
            "client_name": "MCP Test Client",
            "redirect_uris": ["https://client.example/callback"],
            "scope": "records.read booking.read",
        },
    )
    assert registered.status_code == 201
    client_id = registered.get_json()["client_id"]
    assert registered.get_json()["registration_status"] == "pending_admin_approval"

    admin = _login(client, "admin", "admin123")
    approved = client.post(
        f"/api/admin/oauth-clients/{client_id}/decision",
        headers=admin,
        json={"decision": "approve"},
    )
    assert approved.status_code == 200

    verifier = "a" * 64
    challenge = _pkce(verifier)
    request_payload = {
        "client_id": client_id,
        "redirect_uri": "https://client.example/callback",
        "response_type": "code",
        "scope": "records.read booking.read",
        "state": "opaque-state",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    browser = client.get("/oauth/authorize", query_string=request_payload)
    assert browser.status_code == 302
    assert browser.headers["Location"].startswith("/oauth-consent?")

    user = _login(client, "test1", "Shuhealthdoc！")
    authorized = client.post(
        "/oauth/authorize",
        headers=user,
        json={**request_payload, "decision": "approve"},
    )
    assert authorized.status_code == 200
    callback = urlparse(authorized.get_json()["redirect_to"])
    code = parse_qs(callback.query)["code"][0]
    assert parse_qs(callback.query)["state"] == ["opaque-state"]

    exchanged = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": "https://client.example/callback",
            "code_verifier": verifier,
        },
    )
    assert exchanged.status_code == 200
    tokens = exchanged.get_json()
    assert tokens["token_type"] == "Bearer"

    replay_code = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": "https://client.example/callback",
            "code_verifier": verifier,
        },
    )
    assert replay_code.status_code == 400

    verified = client.post(
        "/api/internal/mcp/verify",
        headers={"X-HealthDoc-Internal-Key": "test-mcp-internal-key"},
        json={"token": tokens["access_token"]},
    )
    assert verified.status_code == 200
    assert verified.get_json()["active"] is True
    assert set(verified.get_json()["scopes"]) == {"records.read", "booking.read"}

    rotated = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert rotated.status_code == 200
    rotated_tokens = rotated.get_json()
    assert rotated_tokens["refresh_token"] != tokens["refresh_token"]

    replay_refresh = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert replay_refresh.status_code == 400

    revoked_family = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": rotated_tokens["refresh_token"],
        },
    )
    assert revoked_family.status_code == 400


def test_oauth_rejects_wildcard_and_unregistered_redirects(client):
    wildcard = client.post(
        "/oauth/register",
        json={
            "client_name": "Unsafe",
            "redirect_uris": ["https://*.example.com/callback"],
            "scope": "knowledge.read",
        },
    )
    assert wildcard.status_code == 400
