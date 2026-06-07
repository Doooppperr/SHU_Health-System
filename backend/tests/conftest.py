import pytest

from app import create_app
from app.extensions import db
from app.seed import seed_core_data


@pytest.fixture()
def app():
    app = create_app("testing")

    with app.app_context():
        db.drop_all()
        db.create_all()
        seed_core_data()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    test_client = app.test_client()

    def login_payload(username, password="secret123"):
        captcha_response = test_client.get("/api/auth/captcha")
        assert captcha_response.status_code == 200
        captcha = captcha_response.get_json()
        return {
            "username": username,
            "password": password,
            "captcha_id": captcha["captcha_id"],
            "captcha_answer": captcha["captcha_answer"],
        }

    def register_payload(username, password="secret123", email=None, phone=None):
        captcha_response = test_client.get("/api/auth/captcha")
        assert captcha_response.status_code == 200
        captcha = captcha_response.get_json()
        payload = {
            "username": username,
            "password": password,
            "captcha_id": captcha["captcha_id"],
            "captcha_answer": captcha["captcha_answer"],
        }
        if email is not None:
            payload["email"] = email
        if phone is not None:
            payload["phone"] = phone
        return payload

    test_client.login_payload = login_payload
    test_client.register_payload = register_payload
    return test_client
