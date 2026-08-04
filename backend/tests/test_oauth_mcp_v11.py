import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from urllib.parse import parse_qs, urlparse

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    FriendRelation,
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
    User,
)


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


def test_delegated_account_session_cannot_create_standalone_oauth_grant(
    app,
    client,
):
    with app.app_context():
        actor = User.query.filter_by(username="test1").one()
        subject = User.query.filter_by(username="test2").one()
        relation = FriendRelation.query.filter(
            db.or_(
                db.and_(
                    FriendRelation.user_id == actor.id,
                    FriendRelation.friend_user_id == subject.id,
                ),
                db.and_(
                    FriendRelation.user_id == subject.id,
                    FriendRelation.friend_user_id == actor.id,
                ),
            )
        ).one()
        relation.activate()
        oauth_client = OAuthClient(
            client_id="delegated-oauth-client",
            client_name="Delegated OAuth Regression",
            redirect_uris=["https://client.example/delegated-callback"],
            scopes=["records.read"],
            status="approved",
        )
        db.session.add(oauth_client)
        db.session.commit()
        relation_id = relation.id

    actor_headers = _login(client, "test1", "Shuhealthdoc！")
    switched = client.post(
        f"/api/friends/{relation_id}/switch-session",
        headers=actor_headers,
    )
    assert switched.status_code == 200, switched.get_json()
    delegated_headers = {
        "Authorization": f"Bearer {switched.get_json()['access_token']}"
    }
    verifier = "b" * 64
    denied = client.post(
        "/oauth/authorize",
        headers=delegated_headers,
        json={
            "client_id": "delegated-oauth-client",
            "redirect_uri": "https://client.example/delegated-callback",
            "response_type": "code",
            "scope": "records.read",
            "state": "delegated-state",
            "code_challenge": _pkce(verifier),
            "code_challenge_method": "S256",
            "decision": "approve",
        },
    )
    assert denied.status_code == 403
    assert denied.get_json()["error"] == "access_denied"
    with app.app_context():
        assert OAuthAuthorizationCode.query.filter_by(
            client_id="delegated-oauth-client"
        ).count() == 0


def test_admin_active_state_cycle_permanently_revokes_oauth_grants(app, client):
    registered = client.post(
        "/api/auth/register",
        json=client.register_payload("oauth_state_cycle_v12"),
    )
    assert registered.status_code == 201, registered.get_json()
    login = registered.get_json()
    user_headers = {"Authorization": f"Bearer {login['access_token']}"}
    completed = client.post(
        "/api/profile/me/complete",
        headers=user_headers,
        json={
            "real_name": "测试 OAuth 启停用户",
            "gender": "undisclosed",
            "birth_date": "1990-01-01",
        },
    )
    assert completed.status_code == 200, completed.get_json()

    raw_access = "test-oauth-access-before-active-state-cycle"
    raw_refresh = "test-oauth-refresh-before-active-state-cycle"
    raw_code = "test-oauth-code-before-active-state-cycle"
    now = datetime.now(timezone.utc)
    with app.app_context():
        user = User.query.filter_by(username="oauth_state_cycle_v12").one()
        user_id = user.id
        initial_token_version = user.token_version
        db.session.add(
            OAuthClient(
                client_id="active-state-cycle-client",
                client_name="Active State Cycle Regression",
                redirect_uris=["https://client.example/state-cycle"],
                scopes=["records.read"],
                status="approved",
            )
        )
        db.session.flush()
        db.session.add(OAuthAccessToken(
            token_hash=hashlib.sha256(raw_access.encode()).hexdigest(),
            client_id="active-state-cycle-client",
            user_id=user_id,
            scope="records.read",
            audience="http://localhost/mcp",
            expires_at=now + timedelta(minutes=10),
        ))
        db.session.add(OAuthRefreshToken(
            token_hash=hashlib.sha256(raw_refresh.encode()).hexdigest(),
            family_id="active-state-cycle-family",
            client_id="active-state-cycle-client",
            user_id=user_id,
            scope="records.read",
            audience="http://localhost/mcp",
            expires_at=now + timedelta(days=30),
        ))
        db.session.add(OAuthAuthorizationCode(
            code_hash=hashlib.sha256(raw_code.encode()).hexdigest(),
            client_id="active-state-cycle-client",
            user_id=user_id,
            redirect_uri="https://client.example/state-cycle",
            scope="records.read",
            code_challenge=_pkce("c" * 64),
            expires_at=now + timedelta(minutes=2),
        ))
        db.session.commit()

    admin_headers = _login(client, "admin", "admin123")
    for active in (False, True):
        response = client.put(
            f"/api/users/{user_id}",
            headers=admin_headers,
            json={"is_active": active},
        )
        assert response.status_code == 200, response.get_json()

    assert client.get("/api/users/me", headers=user_headers).status_code == 401
    assert client.post(
        "/api/auth/refresh",
        headers={"Authorization": f"Bearer {login['refresh_token']}"},
    ).status_code == 401
    verified = client.post(
        "/api/internal/mcp/verify",
        headers={"X-HealthDoc-Internal-Key": "test-mcp-internal-key"},
        json={"token": raw_access},
    )
    assert verified.status_code == 200
    assert verified.get_json()["active"] is False
    refreshed = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": "active-state-cycle-client",
            "refresh_token": raw_refresh,
        },
    )
    assert refreshed.status_code == 400
    assert refreshed.get_json()["error"] == "invalid_grant"

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.is_active is True
        assert user.token_version == initial_token_version + 2
        assert OAuthAccessToken.query.filter_by(user_id=user_id).one().revoked_at
        assert OAuthRefreshToken.query.filter_by(user_id=user_id).one().revoked_at
        assert OAuthAuthorizationCode.query.filter_by(
            user_id=user_id
        ).one().consumed_at


def test_client_revoke_or_reject_cannot_revive_codes_or_token_families(
    app,
    client,
):
    registered = client.post(
        "/oauth/register",
        json={
            "client_name": "OAuth Lifecycle Regression",
            "redirect_uris": ["https://client.example/lifecycle"],
            "scope": "records.read",
        },
    )
    assert registered.status_code == 201
    client_id = registered.get_json()["client_id"]
    admin = _login(client, "admin", "admin123")

    def decide(decision):
        response = client.post(
            f"/api/admin/oauth-clients/{client_id}/decision",
            headers=admin,
            json={"decision": decision},
        )
        assert response.status_code == 200, response.get_json()

    decide("approve")
    user = _login(client, "test1", "Shuhealthdoc！")
    verifier = "d" * 64
    authorization_payload = {
        "client_id": client_id,
        "redirect_uri": "https://client.example/lifecycle",
        "response_type": "code",
        "scope": "records.read",
        "code_challenge": _pkce(verifier),
        "code_challenge_method": "S256",
        "decision": "approve",
    }

    def authorize_code():
        response = client.post(
            "/oauth/authorize",
            headers=user,
            json=authorization_payload,
        )
        assert response.status_code == 200, response.get_json()
        return parse_qs(
            urlparse(response.get_json()["redirect_to"]).query
        )["code"][0]

    def exchange(code):
        return client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": "https://client.example/lifecycle",
                "code_verifier": verifier,
            },
        )

    stale_code = authorize_code()
    decide("revoke")
    decide("approve")
    assert exchange(stale_code).status_code == 400

    active_code = authorize_code()
    issued = exchange(active_code)
    assert issued.status_code == 200, issued.get_json()
    access_token = issued.get_json()["access_token"]
    decide("reject")
    during_reject = client.post(
        "/api/internal/mcp/verify",
        headers={"X-HealthDoc-Internal-Key": "test-mcp-internal-key"},
        json={"token": access_token},
    )
    assert during_reject.get_json()["active"] is False
    decide("approve")
    after_reapprove = client.post(
        "/api/internal/mcp/verify",
        headers={"X-HealthDoc-Internal-Key": "test-mcp-internal-key"},
        json={"token": access_token},
    )
    assert after_reapprove.get_json()["active"] is False

    with app.app_context():
        rows = OAuthAuthorizationCode.query.filter_by(
            client_id=client_id
        ).all()
        assert all(row.consumed_at is not None for row in rows)
        assert OAuthAccessToken.query.filter_by(
            client_id=client_id
        ).one().revoked_at is not None
        assert OAuthRefreshToken.query.filter_by(
            client_id=client_id
        ).one().revoked_at is not None


def test_oauth_grants_are_bound_to_user_and_client_security_epochs(app, client):
    registered = client.post(
        "/oauth/register",
        json={
            "client_name": "OAuth Epoch Regression",
            "redirect_uris": ["https://client.example/epoch"],
            "scope": "records.read",
        },
    )
    client_id = registered.get_json()["client_id"]
    admin = _login(client, "admin", "admin123")

    def decide(decision):
        response = client.post(
            f"/api/admin/oauth-clients/{client_id}/decision",
            headers=admin,
            json={"decision": decision},
        )
        assert response.status_code == 200, response.get_json()
        return response.get_json()["item"]

    approved = decide("approve")
    assert approved["approval_version"] == 1
    verifier = "f" * 64

    def authorize(headers):
        response = client.post(
            "/oauth/authorize",
            headers=headers,
            json={
                "client_id": client_id,
                "redirect_uri": "https://client.example/epoch",
                "response_type": "code",
                "scope": "records.read",
                "code_challenge": _pkce(verifier),
                "code_challenge_method": "S256",
                "decision": "approve",
            },
        )
        assert response.status_code == 200, response.get_json()
        return parse_qs(
            urlparse(response.get_json()["redirect_to"]).query
        )["code"][0]

    def exchange(raw_code):
        return client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": raw_code,
                "redirect_uri": "https://client.example/epoch",
                "code_verifier": verifier,
            },
        )

    user_headers = _login(client, "test1", "Shuhealthdoc！")
    stale_user_code = authorize(user_headers)
    with app.app_context():
        user = User.query.filter_by(username="test1").one()
        user.token_version += 1
        db.session.commit()
    rejected_user_code = exchange(stale_user_code)
    assert rejected_user_code.status_code == 400
    assert rejected_user_code.get_json()["error"] == "invalid_grant"

    user_headers = _login(client, "test1", "Shuhealthdoc！")
    issued = exchange(authorize(user_headers))
    assert issued.status_code == 200, issued.get_json()
    issued_tokens = issued.get_json()
    with app.app_context():
        user = User.query.filter_by(username="test1").one()
        user.token_version += 1
        db.session.commit()
    inactive_access = client.post(
        "/api/internal/mcp/verify",
        headers={"X-HealthDoc-Internal-Key": "test-mcp-internal-key"},
        json={"token": issued_tokens["access_token"]},
    )
    assert inactive_access.status_code == 200
    assert inactive_access.get_json()["active"] is False
    inactive_refresh = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": issued_tokens["refresh_token"],
        },
    )
    assert inactive_refresh.status_code == 400
    assert inactive_refresh.get_json()["error"] == "invalid_grant"

    old_client_epoch = decide("revoke")["approval_version"]
    current_client = decide("approve")
    assert current_client["approval_version"] == old_client_epoch + 1
    late_code = "late-commit-client-epoch-code"
    with app.app_context():
        user = User.query.filter_by(username="test1").one()
        db.session.add(
            OAuthAuthorizationCode(
                code_hash=hashlib.sha256(late_code.encode()).hexdigest(),
                client_id=client_id,
                user_id=user.id,
                redirect_uri="https://client.example/epoch",
                scope="records.read",
                code_challenge=_pkce(verifier),
                user_token_version_snapshot=user.token_version,
                client_approval_version_snapshot=old_client_epoch,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
            )
        )
        db.session.commit()
    rejected_client_epoch = exchange(late_code)
    assert rejected_client_epoch.status_code == 400
    assert rejected_client_epoch.get_json()["error"] == "invalid_grant"


def test_oauth_code_and_refresh_are_atomically_consumed_under_concurrency(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "oauth-atomic-consumption.db"
    monkeypatch.setattr(
        TestingConfig,
        "SQLALCHEMY_DATABASE_URI",
        f"sqlite:///{database_path.as_posix()}",
    )
    concurrent_app = create_app("testing")
    raw_code = "concurrent-authorization-code"
    verifier = "e" * 64
    now = datetime.now(timezone.utc)
    with concurrent_app.app_context():
        user = User.query.filter_by(username="test1").one()
        user_id = user.id
        db.session.add(
            OAuthClient(
                client_id="atomic-code-client",
                client_name="Atomic Code Client",
                redirect_uris=["https://client.example/atomic-code"],
                scopes=["records.read"],
                status="approved",
            )
        )
        db.session.flush()
        code_row = OAuthAuthorizationCode(
            code_hash=hashlib.sha256(raw_code.encode()).hexdigest(),
            client_id="atomic-code-client",
            user_id=user_id,
            redirect_uri="https://client.example/atomic-code",
            scope="records.read",
            code_challenge=_pkce(verifier),
            expires_at=now + timedelta(minutes=2),
        )
        db.session.add(code_row)
        db.session.commit()
        code_id = code_row.id

    from app.oauth import routes as oauth_routes

    original_code_consumer = oauth_routes._consume_authorization_code
    code_barrier = Barrier(2)

    def synchronized_code_consumer(code_id, consumed_at):
        code_barrier.wait(timeout=10)
        return original_code_consumer(code_id, consumed_at)

    monkeypatch.setattr(
        oauth_routes,
        "_consume_authorization_code",
        synchronized_code_consumer,
    )

    code_form = {
        "grant_type": "authorization_code",
        "client_id": "atomic-code-client",
        "code": raw_code,
        "redirect_uri": "https://client.example/atomic-code",
        "code_verifier": verifier,
    }

    def post_token(form):
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post("/oauth/token", data=form)
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        code_results = [
            future.result(timeout=20)
            for future in (
                executor.submit(post_token, code_form),
                executor.submit(post_token, code_form),
            )
        ]

    assert sorted(status for status, _payload in code_results) == [200, 400]
    assert next(
        payload for status, payload in code_results if status == 400
    )["error"] == "invalid_grant"
    with concurrent_app.app_context():
        assert db.session.get(OAuthAuthorizationCode, code_id).consumed_at is not None
        assert OAuthAccessToken.query.filter_by(
            client_id="atomic-code-client"
        ).count() == 1
        assert OAuthRefreshToken.query.filter_by(
            client_id="atomic-code-client"
        ).count() == 1

    monkeypatch.setattr(
        oauth_routes,
        "_consume_authorization_code",
        original_code_consumer,
    )
    raw_refresh = "concurrent-refresh-token"
    refresh_family = "atomic-refresh-family"
    with concurrent_app.app_context():
        db.session.add(
            OAuthClient(
                client_id="atomic-refresh-client",
                client_name="Atomic Refresh Client",
                redirect_uris=["https://client.example/atomic-refresh"],
                scopes=["records.read"],
                status="approved",
            )
        )
        db.session.flush()
        db.session.add(
            OAuthRefreshToken(
                token_hash=hashlib.sha256(raw_refresh.encode()).hexdigest(),
                family_id=refresh_family,
                client_id="atomic-refresh-client",
                user_id=user_id,
                scope="records.read",
                audience="http://localhost/mcp",
                expires_at=now + timedelta(days=30),
            )
        )
        db.session.commit()

    original_refresh_consumer = oauth_routes._consume_refresh_token
    refresh_barrier = Barrier(2)

    def synchronized_refresh_consumer(token_id, consumed_at):
        refresh_barrier.wait(timeout=10)
        return original_refresh_consumer(token_id, consumed_at)

    monkeypatch.setattr(
        oauth_routes,
        "_consume_refresh_token",
        synchronized_refresh_consumer,
    )
    refresh_form = {
        "grant_type": "refresh_token",
        "client_id": "atomic-refresh-client",
        "refresh_token": raw_refresh,
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        refresh_results = [
            future.result(timeout=20)
            for future in (
                executor.submit(post_token, refresh_form),
                executor.submit(post_token, refresh_form),
            )
        ]

    assert sorted(status for status, _payload in refresh_results) == [200, 400]
    assert next(
        payload for status, payload in refresh_results if status == 400
    )["error"] == "invalid_grant"
    with concurrent_app.app_context():
        family_rows = OAuthRefreshToken.query.filter_by(
            family_id=refresh_family
        ).all()
        assert len(family_rows) == 2
        assert all(row.revoked_at is not None for row in family_rows)
        assert sum(row.used_at is not None for row in family_rows) == 1
        assert OAuthAccessToken.query.filter_by(
            client_id="atomic-refresh-client"
        ).count() == 1
