from datetime import datetime, timezone

from app.extensions import db
from app.models import Comment, IndicatorDict, NotificationOutbox, User


PASSWORD = "Shuhealthdoc！"


def login(client, username="test1", password=PASSWORD):
    response = client.post("/api/auth/login", json=client.login_payload(username, password))
    assert response.status_code == 200
    data = response.get_json()
    return data, {"Authorization": f"Bearer {data['access_token']}"}


def test_active_password_change_requires_current_password_and_revokes_old_tokens(app, client):
    auth, headers = login(client)
    sent = client.post("/api/auth/password-change/code", headers=headers)
    assert sent.status_code == 200
    challenge = sent.get_json()
    assert challenge["verification_code"].isdigit()

    wrong = client.post("/api/auth/password-change/confirm", headers=headers, json={
        "challenge_id": challenge["challenge_id"],
        "verification_code": challenge["verification_code"],
        "current_password": "错误密码",
        "new_password": "new-secret-123",
    })
    assert wrong.status_code == 400
    changed = client.post("/api/auth/password-change/confirm", headers=headers, json={
        "challenge_id": challenge["challenge_id"],
        "verification_code": challenge["verification_code"],
        "current_password": PASSWORD,
        "new_password": "new-secret-123",
    })
    assert changed.status_code == 200
    assert client.get("/api/users/me", headers=headers).status_code == 401
    assert client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {auth['refresh_token']}"}).status_code == 401
    login(client, password="new-secret-123")


def test_password_reset_uses_username_email_captcha_and_rate_limits_delivery(app, client):
    with app.app_context():
        user = User.query.filter_by(username="test2").one()
        email = user.email
    captcha = client.get("/api/auth/captcha").get_json()
    payload = {"username": "test2", "email": email, "captcha_id": captcha["captcha_id"], "captcha_answer": captcha["captcha_answer"]}
    sent = client.post("/api/auth/password-reset/code", json=payload)
    assert sent.status_code == 200
    data = sent.get_json()
    assert data["verification_code"].isdigit()

    captcha = client.get("/api/auth/captcha").get_json()
    payload.update(captcha_id=captcha["captcha_id"], captcha_answer=captcha["captcha_answer"])
    again = client.post("/api/auth/password-reset/code", json=payload)
    assert again.status_code == 200
    with app.app_context():
        assert NotificationOutbox.query.filter_by(event_type="password_verification_code", recipient=email).count() == 1

    reset = client.post("/api/auth/password-reset/confirm", json={
        "challenge_id": data["challenge_id"], "verification_code": data["verification_code"], "new_password": "reset-123456"
    })
    assert reset.status_code == 200
    login(client, "test2", "reset-123456")


def test_measurements_accept_two_decimals_and_reject_more(app, client):
    _, headers = login(client, "test3")
    with app.app_context():
        indicator = IndicatorDict.query.filter_by(code="HEIGHT").one()
        indicator_id = indicator.id
    base = {"indicator_dict_id": indicator_id, "measured_at": datetime.now(timezone.utc).isoformat()}
    created = client.post("/api/self-measurements", headers=headers, json={**base, "value": 175.99})
    assert created.status_code == 201
    assert created.get_json()["item"]["value"] == 175.99
    invalid = client.post("/api/self-measurements", headers=headers, json={**base, "value": 175.999})
    assert invalid.status_code == 400
    assert "两位" in invalid.get_json()["message"]


def test_administrators_cannot_request_self_service_password_change(client):
    _, headers = login(client, "admin", "admin123")
    response = client.post("/api/auth/password-change/code", headers=headers)
    assert response.status_code == 403


def test_institution_reply_requires_admin_review_and_notifies_comment_owner(app, client):
    with app.app_context():
        comment = Comment.query.filter_by(is_visible=True).filter(~Comment.reply.has()).first()
        assert comment is not None
        comment_id = comment.id
        owner_username = comment.user.username
        staff_username = comment.institution.administrators[0].username
    _, staff_headers = login(client, staff_username)
    submitted = client.post(f"/api/comments/{comment_id}/reply", headers=staff_headers, json={"content": "感谢您的评价，我们会继续改进服务流程。"})
    assert submitted.status_code == 201
    reply_id = submitted.get_json()["item"]["id"]

    _, owner_headers = login(client, owner_username)
    assert client.get("/api/comments/mine/unread-replies", headers=owner_headers).get_json()["count"] == 0

    _, admin_headers = login(client, "admin", "admin123")
    approved = client.post(f"/api/comments/replies/{reply_id}/approve", headers=admin_headers)
    assert approved.status_code == 200
    assert client.get("/api/comments/mine/unread-replies", headers=owner_headers).get_json()["count"] == 1
    mine = client.get("/api/comments/mine", headers=owner_headers).get_json()["items"]
    assert next(item for item in mine if item["id"] == comment_id)["reply"]["is_unread"] is True
    assert client.post("/api/comments/mine/replies/read", headers=owner_headers).status_code == 200
    assert client.get("/api/comments/mine/unread-replies", headers=owner_headers).get_json()["count"] == 0
