from app import create_app
from app.demo_v7 import _ensure_demo_branches
from app.extensions import db
from app.models import Institution, Organization, User
from app.seed import seed_admin_user, seed_core_data


DEFAULT_ADMIN_USERNAME = "seed-security-admin"
DEFAULT_ADMIN_PASSWORD = "admin123"
SECURE_ADMIN_PASSWORD = "Secure-seed-admin-2026!"


def _create_seeded_app(monkeypatch):
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME)
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)
    monkeypatch.setenv("DEFAULT_ADMIN_EMAIL", "seed-security-admin@example.test")
    app = create_app("testing")
    app.config["REQUIRE_SECURE_DEFAULT_ADMIN"] = False
    return app


def _login(
    client,
    password=DEFAULT_ADMIN_PASSWORD,
    *,
    username=DEFAULT_ADMIN_USERNAME,
):
    captcha = client.get("/api/auth/captcha").get_json()
    return client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "captcha_id": captcha["captcha_id"],
            "captcha_answer": captcha["captcha_answer"],
        },
    )


def test_seed_core_data_does_not_reactivate_suspended_default_admin_or_old_jwt(
    monkeypatch,
):
    app = _create_seeded_app(monkeypatch)
    client = app.test_client()
    login = _login(client)
    assert login.status_code == 200
    old_headers = {"Authorization": f"Bearer {login.get_json()['access_token']}"}

    with app.app_context():
        admin = User.query.filter_by(username=DEFAULT_ADMIN_USERNAME).one()
        original_token_version = admin.token_version
        admin.is_active = False
        db.session.commit()

        seed_core_data()

        db.session.expire_all()
        admin = User.query.filter_by(username=DEFAULT_ADMIN_USERNAME).one()
        assert admin.is_active is False
        assert admin.token_version == original_token_version

    assert client.get("/api/admin/dashboard", headers=old_headers).status_code == 403
    blocked_login = _login(client)
    assert blocked_login.status_code == 403
    assert blocked_login.get_json()["code"] == "ACCOUNT_INACTIVE"


def test_seed_admin_security_repairs_bump_token_version_and_revoke_old_jwt(
    monkeypatch,
):
    app = _create_seeded_app(monkeypatch)
    client = app.test_client()
    login = _login(client)
    assert login.status_code == 200
    old_headers = {"Authorization": f"Bearer {login.get_json()['access_token']}"}

    with app.app_context():
        admin = User.query.filter_by(username=DEFAULT_ADMIN_USERNAME).one()
        original_token_version = admin.token_version
        admin.role = "user"
        admin.health_id = "HID-8Q7W6E5R"
        db.session.commit()

        monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", SECURE_ADMIN_PASSWORD)
        app.config["REQUIRE_SECURE_DEFAULT_ADMIN"] = True
        seed_admin_user()

        db.session.expire_all()
        admin = User.query.filter_by(username=DEFAULT_ADMIN_USERNAME).one()
        assert admin.role == "admin"
        assert admin.managed_institution_id is None
        assert admin.health_id is None
        assert admin.check_password(SECURE_ADMIN_PASSWORD)
        assert admin.token_version == original_token_version + 1

    revoked = client.get("/api/admin/dashboard", headers=old_headers)
    assert revoked.status_code == 401
    assert revoked.get_json()["code"] == "TOKEN_REVOKED"
    assert _login(client).status_code == 401
    assert _login(client, SECURE_ADMIN_PASSWORD).status_code == 200


def test_demo_catalog_seed_does_not_reactivate_disabled_organization_or_branch(
    monkeypatch,
):
    app = _create_seeded_app(monkeypatch)
    client = app.test_client()

    with app.app_context():
        organizations = Organization.query.order_by(Organization.id).all()
        assert len(organizations) >= 2
        disabled_organization = organizations[0]
        disabled_branch = organizations[1].branches[0]
        organization_staff = disabled_organization.branches[0].administrator
        branch_staff = disabled_branch.administrator
        disabled_organization_id = disabled_organization.id
        disabled_branch_id = disabled_branch.id
        disabled_branch_staff_id = branch_staff.id
        organization_staff_username = organization_staff.username
        branch_staff_username = branch_staff.username

    organization_login = _login(
        client,
        username=organization_staff_username,
        password="Shuhealthdoc！",
    )
    branch_login = _login(
        client,
        username=branch_staff_username,
        password="Shuhealthdoc！",
    )
    assert organization_login.status_code == 200
    assert branch_login.status_code == 200
    organization_headers = {
        "Authorization": (
            f"Bearer {organization_login.get_json()['access_token']}"
        ),
    }
    branch_headers = {
        "Authorization": f"Bearer {branch_login.get_json()['access_token']}",
    }

    with app.app_context():
        disabled_organization = db.session.get(
            Organization,
            disabled_organization_id,
        )
        disabled_branch = db.session.get(Institution, disabled_branch_id)
        disabled_branch_staff = db.session.get(User, disabled_branch_staff_id)
        disabled_organization.is_active = False
        disabled_branch.is_active = False
        disabled_branch_staff.is_active = False
        db.session.commit()

        _ensure_demo_branches()
        db.session.commit()
        seed_core_data()

        db.session.expire_all()
        assert db.session.get(Organization, disabled_organization_id).is_active is False
        assert db.session.get(Institution, disabled_branch_id).is_active is False
        assert db.session.get(User, disabled_branch_staff_id).is_active is False

    assert client.get(
        "/api/org/dashboard",
        headers=organization_headers,
    ).status_code == 403
    assert client.get(
        "/api/org/dashboard",
        headers=branch_headers,
    ).status_code == 403
    assert _login(
        client,
        username=organization_staff_username,
        password="Shuhealthdoc！",
    ).status_code == 403
    assert _login(
        client,
        username=branch_staff_username,
        password="Shuhealthdoc！",
    ).status_code == 403
