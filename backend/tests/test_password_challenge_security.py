from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from sqlalchemy import create_engine, inspect, text, update
from flask_jwt_extended import create_access_token

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    Institution,
    NotificationOutbox,
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
    PasswordVerificationChallenge,
    User,
)
from scripts.migrate_schema_v12 import (
    _upgrade_stamped_v12_password_challenges,
)
from app.services.password_challenges import increment_user_security_epochs


PASSWORD = "Shuhealthdoc！"


def _login(client, username, password=PASSWORD):
    response = client.post(
        "/api/auth/login",
        json=client.login_payload(username, password),
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    return {"Authorization": f"Bearer {payload['access_token']}"}


def _request_reset(client, app, username):
    with app.app_context():
        email = User.query.filter_by(username=username).one().email
    captcha = client.get("/api/auth/captcha").get_json()
    response = client.post(
        "/api/auth/password-reset/code",
        json={
            "username": username,
            "email": email,
            "captcha_id": captcha["captcha_id"],
            "captcha_answer": captcha["captcha_answer"],
        },
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload["verification_code"].isdigit()
    return payload


def _reset_request_payload(client, app, username):
    with app.app_context():
        email = User.query.filter_by(username=username).one().email
    captcha = client.get("/api/auth/captcha").get_json()
    return {
        "username": username,
        "email": email,
        "captcha_id": captcha["captcha_id"],
        "captcha_answer": captcha["captcha_answer"],
    }


def _confirm_reset(client, challenge, new_password):
    return client.post(
        "/api/auth/password-reset/confirm",
        json={
            "challenge_id": challenge["challenge_id"],
            "verification_code": challenge["verification_code"],
            "new_password": new_password,
        },
    )


def _wrong_code(challenge, offset=1):
    value = (int(challenge["verification_code"]) + offset) % 1_000_000
    return f"{value:06d}"


def _create_file_app(monkeypatch, tmp_path, filename):
    database_path = tmp_path / filename
    monkeypatch.setattr(
        TestingConfig,
        "SQLALCHEMY_DATABASE_URI",
        f"sqlite:///{database_path.as_posix()}",
    )
    monkeypatch.setattr(TestingConfig, "UPLOAD_DIR", str(tmp_path / "uploads"))
    return create_app("testing")


def test_admin_password_change_consumes_old_challenge_and_epoch_binds_new_codes(
    app,
    client,
):
    challenge = _request_reset(client, app, "test1")
    with app.app_context():
        user = User.query.filter_by(username="test1").one()
        row = PasswordVerificationChallenge.query.filter_by(
            public_id=challenge["challenge_id"],
        ).one()
        assert row.token_version_snapshot == user.token_version
        user_id = user.id
        oauth_client = OAuthClient(
            client_id="admin-password-challenge-security",
            client_name="管理员改密撤销回归",
            redirect_uris=["https://client.example/security-callback"],
            scopes=["records.read"],
            status="approved",
        )
        db.session.add(oauth_client)
        db.session.flush()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        db.session.add_all([
            OAuthAuthorizationCode(
                code_hash="a" * 64,
                client_id=oauth_client.client_id,
                user_id=user.id,
                redirect_uri="https://client.example/security-callback",
                scope="records.read",
                code_challenge="challenge",
                user_token_version_snapshot=user.token_version,
                client_approval_version_snapshot=oauth_client.approval_version,
                expires_at=expires_at,
            ),
            OAuthAccessToken(
                token_hash="b" * 64,
                client_id=oauth_client.client_id,
                user_id=user.id,
                scope="records.read",
                audience="healthdoc-mcp",
                user_token_version_snapshot=user.token_version,
                client_approval_version_snapshot=oauth_client.approval_version,
                expires_at=expires_at,
            ),
            OAuthRefreshToken(
                token_hash="c" * 64,
                family_id="admin-password-security-family",
                client_id=oauth_client.client_id,
                user_id=user.id,
                scope="records.read",
                audience="healthdoc-mcp",
                user_token_version_snapshot=user.token_version,
                client_approval_version_snapshot=oauth_client.approval_version,
                expires_at=expires_at,
            ),
        ])
        db.session.commit()

    admin_headers = _login(client, "admin", "admin123")
    changed = client.post(
        f"/api/users/{user_id}/password",
        headers=admin_headers,
        json={"password": "AdminChangedPassword123!"},
    )
    assert changed.status_code == 200, changed.get_json()

    with app.app_context():
        row = PasswordVerificationChallenge.query.filter_by(
            public_id=challenge["challenge_id"],
        ).one()
        assert row.consumed_at is not None
        assert OAuthAuthorizationCode.query.filter_by(
            user_id=user_id,
            consumed_at=None,
        ).count() == 0
        assert OAuthAccessToken.query.filter_by(
            user_id=user_id,
            revoked_at=None,
        ).count() == 0
        assert OAuthRefreshToken.query.filter_by(
            user_id=user_id,
            revoked_at=None,
        ).count() == 0

    stale = _confirm_reset(client, challenge, "StaleChallengePassword123!")
    assert stale.status_code == 400
    assert stale.get_json()["code"] == "PASSWORD_CODE_INVALID"
    _login(client, "test1", "AdminChangedPassword123!")


def test_active_state_and_institution_reset_consume_all_outstanding_challenges(
    app,
    client,
):
    admin_headers = _login(client, "admin", "admin123")

    user_challenge = _request_reset(client, app, "test2")
    with app.app_context():
        user = User.query.filter_by(username="test2").one()
        user_id = user.id
    deactivated = client.put(
        f"/api/users/{user_id}",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert deactivated.status_code == 200, deactivated.get_json()
    assert _confirm_reset(
        client,
        user_challenge,
        "InactiveChallengePassword123!",
    ).status_code == 400

    institution_challenge = _request_reset(
        client,
        app,
        "institution1_staff1",
    )
    with app.app_context():
        institution = Institution.query.join(
            User,
            User.managed_institution_id == Institution.id,
        ).filter(User.username == "institution1_staff1").one()
        institution_id = institution.id
        email = institution.notification_email
    reset = client.post(
        f"/api/admin/institutions/{institution_id}/account/reset",
        headers=admin_headers,
        json={
            "password": "InstitutionResetPassword123!",
            "email": email,
        },
    )
    assert reset.status_code == 200, reset.get_json()
    stale = _confirm_reset(
        client,
        institution_challenge,
        "StaleInstitutionChallenge123!",
    )
    assert stale.status_code == 400
    assert stale.get_json()["code"] == "PASSWORD_CODE_INVALID"


def test_admin_email_change_bumps_epoch_and_invalidates_recovery_code(
    app,
    client,
):
    user_headers = _login(client, "test3")
    challenge = _request_reset(client, app, "test3")
    with app.app_context():
        user = User.query.filter_by(username="test3").one()
        user_id = user.id
        initial_token_version = user.token_version

    admin_headers = _login(client, "admin", "admin123")
    changed = client.put(
        f"/api/users/{user_id}",
        headers=admin_headers,
        json={"email": "test3-admin-corrected@example.test"},
    )
    assert changed.status_code == 200, changed.get_json()
    assert client.get("/api/users/me", headers=user_headers).status_code == 401

    with app.app_context():
        user = db.session.get(User, user_id)
        row = PasswordVerificationChallenge.query.filter_by(
            public_id=challenge["challenge_id"],
        ).one()
        assert user.email == "test3-admin-corrected@example.test"
        assert user.email_verified_at is None
        assert user.token_version == initial_token_version + 1
        assert row.consumed_at is not None

    stale = _confirm_reset(
        client,
        challenge,
        "AdminEmailStaleChallenge123!",
    )
    assert stale.status_code == 400
    assert stale.get_json()["code"] == "PASSWORD_CODE_INVALID"


def test_concurrent_password_reset_confirmation_has_exactly_one_winner(
    monkeypatch,
    tmp_path,
):
    app = _create_file_app(
        monkeypatch,
        tmp_path,
        "concurrent-password-reset.db",
    )
    setup_client = app.test_client()
    # Attach the same test helper exposed by the normal client fixture.
    def login_payload(username, password="secret123"):
        captcha = setup_client.get("/api/auth/captcha").get_json()
        return {
            "username": username,
            "password": password,
            "captcha_id": captcha["captcha_id"],
            "captcha_answer": captcha["captcha_answer"],
        }

    setup_client.login_payload = login_payload
    challenge = _request_reset(setup_client, app, "test3")
    with app.app_context():
        user = User.query.filter_by(username="test3").one()
        initial_token_version = user.token_version

    barrier = Barrier(2)

    def confirm(candidate_password):
        thread_client = app.test_client()
        barrier.wait(timeout=10)
        response = _confirm_reset(
            thread_client,
            challenge,
            candidate_password,
        )
        return response.status_code, response.get_json()

    candidates = (
        "ConcurrentWinnerPasswordA123!",
        "ConcurrentWinnerPasswordB123!",
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm, candidates))

    assert sorted(status for status, _payload in results) == [200, 400]
    loser_payload = next(payload for status, payload in results if status == 400)
    assert loser_payload["code"] == "PASSWORD_CODE_INVALID"
    with app.app_context():
        user = User.query.filter_by(username="test3").one()
        row = PasswordVerificationChallenge.query.filter_by(
            public_id=challenge["challenge_id"],
        ).one()
        assert user.token_version == initial_token_version + 1
        assert sum(user.check_password(candidate) for candidate in candidates) == 1
        assert row.consumed_at is not None


def test_two_concurrent_wrong_codes_each_consume_one_attempt(monkeypatch, tmp_path):
    app = _create_file_app(
        monkeypatch,
        tmp_path,
        "two-wrong-password-attempts.db",
    )
    challenge = _request_reset(app.test_client(), app, "test4")
    barrier = Barrier(2)

    def submit_wrong_code(suffix):
        thread_client = app.test_client()
        barrier.wait(timeout=10)
        response = thread_client.post(
            "/api/auth/password-reset/confirm",
            json={
                "challenge_id": challenge["challenge_id"],
                "verification_code": _wrong_code(challenge, suffix),
                "new_password": "WrongCodeMustNotChangePassword123!",
            },
        )
        return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit_wrong_code, (1, 2)))

    assert [status for status, _payload in results] == [400, 400]
    assert {
        payload["code"] for _status, payload in results
    } == {"PASSWORD_CODE_INCORRECT"}
    with app.app_context():
        row = PasswordVerificationChallenge.query.filter_by(
            public_id=challenge["challenge_id"],
        ).one()
        assert row.attempt_count == 2
        assert row.consumed_at is None


def test_concurrent_wrong_code_budget_is_globally_capped_at_five(
    monkeypatch,
    tmp_path,
):
    app = _create_file_app(
        monkeypatch,
        tmp_path,
        "wrong-password-attempt-budget.db",
    )
    challenge = _request_reset(app.test_client(), app, "test5")
    worker_count = 8
    barrier = Barrier(worker_count)

    def submit_wrong_code(index):
        thread_client = app.test_client()
        barrier.wait(timeout=10)
        response = thread_client.post(
            "/api/auth/password-reset/confirm",
            json={
                "challenge_id": challenge["challenge_id"],
                "verification_code": _wrong_code(challenge, index + 1),
                "new_password": "WrongBudgetMustNotChangePassword123!",
            },
        )
        return response.status_code, response.get_json()["code"]

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(submit_wrong_code, range(worker_count)))

    assert all(status == 400 for status, _code in results)
    assert sum(
        code == "PASSWORD_CODE_INCORRECT" for _status, code in results
    ) == 5
    assert sum(
        code == "PASSWORD_CODE_INVALID" for _status, code in results
    ) == worker_count - 5
    with app.app_context():
        row = PasswordVerificationChallenge.query.filter_by(
            public_id=challenge["challenge_id"],
        ).one()
        assert row.attempt_count == 5
        assert row.consumed_at is None


def test_concurrent_challenge_issuance_is_singleton_and_respects_hourly_budget(
    monkeypatch,
    tmp_path,
):
    app = _create_file_app(
        monkeypatch,
        tmp_path,
        "concurrent-password-challenge-issuance.db",
    )
    now = datetime.now(timezone.utc)
    with app.app_context():
        user = User.query.filter_by(username="test1").one()
        user_id = user.id
        token_version = user.token_version
        for index in range(4):
            historical = PasswordVerificationChallenge(
                user_id=user.id,
                purpose="reset",
                email_snapshot=user.email,
                request_ip_hash=f"historical-ip-{index}",
                token_version_snapshot=user.token_version,
                expires_at=now + timedelta(minutes=5),
                consumed_at=now - timedelta(minutes=2),
                created_at=now - timedelta(minutes=5, seconds=index),
            )
            historical.set_code(f"{index:06d}")
            db.session.add(historical)
        db.session.commit()

    clients = (app.test_client(), app.test_client())
    payloads = tuple(
        _reset_request_payload(thread_client, app, "test1")
        for thread_client in clients
    )
    barrier = Barrier(2)

    def issue(index):
        barrier.wait(timeout=10)
        response = clients[index].post(
            "/api/auth/password-reset/code",
            json=payloads[index],
        )
        return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(issue, range(2)))

    assert [status for status, _payload in results] == [200, 200]
    challenge_ids = {payload["challenge_id"] for _status, payload in results}
    assert len(challenge_ids) == 1
    assert sum(
        bool(payload.get("verification_code"))
        for _status, payload in results
    ) == 1
    with app.app_context():
        rows = PasswordVerificationChallenge.query.filter_by(
            user_id=user_id,
        ).all()
        assert len(rows) == 5
        active = [
            row
            for row in rows
            if row.purpose == "reset"
            and row.consumed_at is None
            and row.token_version_snapshot == token_version
        ]
        assert len(active) == 1
        assert active[0].public_id in challenge_ids
        assert NotificationOutbox.query.filter_by(
            event_type="password_verification_code",
        ).filter(
            NotificationOutbox.idempotency_key
            == f"password-code:{active[0].public_id}"
        ).count() == 1


def test_normal_refresh_reloads_epoch_and_rejects_stale_source_claims(app, client):
    with app.app_context():
        user_id = User.query.filter_by(username="test6").one().id

    stale_references = []
    changed = {"done": False}

    @app.before_request
    def mutate_epoch_after_identity_map_load():
        from flask import request

        if request.path != "/api/auth/refresh" or changed["done"]:
            return None
        stale_references.append(db.session.get(User, user_id))
        db.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(token_version=User.token_version + 1),
            execution_options={"synchronize_session": False},
        )
        changed["done"] = True
        return None

    login = client.post(
        "/api/auth/login",
        json=client.login_payload("test6", PASSWORD),
    )
    assert login.status_code == 200, login.get_json()
    refresh_token = login.get_json()["refresh_token"]

    refreshed = client.post(
        "/api/auth/refresh",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert refreshed.status_code == 401
    assert refreshed.get_json()["code"] == "TOKEN_REVOKED"


def test_concurrent_security_epoch_updates_add_two_and_revoke_middle_jwt(
    monkeypatch,
    tmp_path,
):
    app = _create_file_app(
        monkeypatch,
        tmp_path,
        "concurrent-user-security-epochs.db",
    )
    with app.app_context():
        user = User.query.filter_by(username="test2").one()
        user_id = user.id
        initial_token_version = user.token_version
        initial_booking_version = user.booking_authorization_version

    barrier = Barrier(2)

    def bump_epoch():
        with app.app_context():
            connection_id = id(
                db.session.connection().connection.driver_connection
            )
            barrier.wait(timeout=10)
            versions = increment_user_security_epochs(
                user_id,
                booking_authorization_version=True,
            )[user_id]
            middle_token = None
            if versions["token_version"] == initial_token_version + 1:
                middle_token = create_access_token(
                    identity=str(user_id),
                    additional_claims={
                        "role": "user",
                        "token_version": versions["token_version"],
                    },
                )
            db.session.commit()
            return connection_id, versions, middle_token

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: bump_epoch(), range(2)))

    assert len({connection_id for connection_id, _versions, _token in results}) == 2
    assert sorted(
        versions["token_version"]
        for _connection_id, versions, _token in results
    ) == [initial_token_version + 1, initial_token_version + 2]
    middle_token = next(
        token for _connection_id, _versions, token in results if token
    )
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.token_version == initial_token_version + 2
        assert (
            user.booking_authorization_version
            == initial_booking_version + 2
        )
    rejected = app.test_client().get(
        "/api/users/me",
        headers={"Authorization": f"Bearer {middle_token}"},
    )
    assert rejected.status_code == 401
    assert rejected.get_json()["code"] == "TOKEN_REVOKED"


def test_early_v12_additive_migration_invalidates_legacy_challenges(tmp_path):
    database_path = tmp_path / "early-v12.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE users ("
                    "id INTEGER PRIMARY KEY, token_version INTEGER NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE password_verification_challenges ("
                    "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
                    "consumed_at DATETIME)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(32) PRIMARY KEY)"
                )
            )
            connection.execute(
                text("INSERT INTO users (id, token_version) VALUES (1, 7)")
            )
            connection.execute(
                text(
                    "INSERT INTO password_verification_challenges "
                    "(id, user_id, consumed_at) VALUES (1, 1, NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES ('20260730_schema_v12')"
                )
            )
            _upgrade_stamped_v12_password_challenges(connection)

            columns = {
                column["name"]
                for column in inspect(connection).get_columns(
                    "password_verification_challenges"
                )
            }
            assert "token_version_snapshot" in columns
            migrated = connection.execute(
                text(
                    "SELECT token_version_snapshot, consumed_at "
                    "FROM password_verification_challenges WHERE id=1"
                )
            ).one()
            assert migrated.token_version_snapshot == 7
            assert migrated.consumed_at is not None
    finally:
        engine.dispose()
