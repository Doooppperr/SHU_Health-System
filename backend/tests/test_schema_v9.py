from datetime import datetime, timezone
from collections import Counter
import shutil
import sqlite3

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Comment, IndicatorDict, Institution, NotificationOutbox, User
from scripts import notification_worker
from scripts.refresh_demo_media import (
    BACKEND_ROOT,
    apply as apply_demo_media,
    validate as validate_demo_media,
)


PASSWORD = "Shuhealthdoc！"


def login(client, username="test1", password=PASSWORD):
    response = client.post("/api/auth/login", json=client.login_payload(username, password))
    assert response.status_code == 200
    data = response.get_json()
    return data, {"Authorization": f"Bearer {data['access_token']}"}


def secure_email_change(client, headers, email, password=PASSWORD):
    challenge = client.post(
        "/api/auth/password-change/code",
        headers=headers,
    ).get_json()
    return client.put(
        "/api/auth/email",
        headers=headers,
        json={
            "email": email,
            "current_password": password,
            "challenge_id": challenge["challenge_id"],
            "verification_code": challenge["verification_code"],
        },
    )


def test_active_password_change_requires_current_password_and_revokes_old_tokens(app, client, monkeypatch):
    auth, headers = login(client)
    sent = client.post("/api/auth/password-change/code", headers=headers)
    assert sent.status_code == 200
    challenge = sent.get_json()
    assert challenge["verification_code"].isdigit()
    delivered_to = []
    monkeypatch.setattr(
        notification_worker,
        "_send",
        lambda _app, row: delivered_to.append(row.recipient) or f"mock-{row.id}",
    )
    with app.app_context():
        assert notification_worker.run_batch(app) == (1, 1)
        assert delivered_to == [User.query.filter_by(username="test1").one().email]

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


def test_user_email_change_sends_old_and_new_notifications(app, client):
    _, headers = login(client, "test4")
    with app.app_context():
        old_email = User.query.filter_by(username="test4").one().email
    response = secure_email_change(client, headers, "new-user@example.test")
    assert response.status_code == 200
    assert response.get_json()["user"]["email"] == "new-user@example.test"
    with app.app_context():
        assert User.query.filter_by(username="test4").one().email == "new-user@example.test"
        old_rows = NotificationOutbox.query.filter_by(
            event_type="account_email_changed_old", recipient=old_email,
        ).all()
        new_rows = NotificationOutbox.query.filter_by(
            event_type="account_email_changed_new", recipient="new-user@example.test",
        ).all()
        assert len(old_rows) == 1
        assert len(new_rows) == 1


def test_institution_has_one_account_and_email_change_stays_synchronized(app, client):
    with app.app_context():
        institution = Institution.query.order_by(Institution.id).first()
        old_email = institution.notification_email
        duplicate = User(
            username="branch-shared-mail-test",
            role="institution_admin",
            email="legacy-manager@example.test",
            managed_institution_id=institution.id,
        )
        duplicate.set_password("secret123")
        db.session.add(duplicate)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        staff_username = institution.administrator.username
        institution_id = institution.id
    _, headers = login(client, staff_username)
    response = secure_email_change(client, headers, "branch-new@example.test")
    assert response.status_code == 200
    assert response.get_json()["user"]["email"] == "branch-new@example.test"
    with app.app_context():
        institution = db.session.get(Institution, institution_id)
        assert institution.notification_email == "branch-new@example.test"
        managers = User.query.filter_by(
            managed_institution_id=institution_id,
            role="institution_admin",
        ).all()
        assert len(managers) == 1
        assert managers[0].email == "branch-new@example.test"
        assert NotificationOutbox.query.filter_by(
            event_type="account_email_changed_old", recipient=old_email,
        ).count() == 1
        assert NotificationOutbox.query.filter_by(
            event_type="account_email_changed_new", recipient="branch-new@example.test",
        ).count() == 1


def test_system_administrator_cannot_change_email(client):
    _, headers = login(client, "admin", "admin123")
    response = client.put("/api/auth/email", headers=headers, json={"email": "admin-new@example.test"})
    assert response.status_code == 403
    assert "不能自助修改" in response.get_json()["message"]


def test_report_media_manifest_is_complete_and_clean():
    items = validate_demo_media(BACKEND_ROOT / "uploads")
    assert len(items) == 34
    assert sum(item["kind"] == "report_attachment" for item in items) == 19
    assert sum(item["kind"] == "institution_cover" for item in items) == 15
    report_items = [item for item in items if item["kind"] == "report_attachment"]
    assert all(
        item.get("asset_type_code")
        and item.get("annotation_text")
        and item.get("modality")
        for item in report_items
    )
    assert Counter(item["asset_type_code"] for item in report_items) == {
        "US_THYROID": 1,
        "US_ABDOMEN": 3,
        "SPIROMETRY": 2,
        "ECG_12": 5,
        "CHEST_IMAGE": 3,
        "ECHO_HEART": 2,
        "BLOOD_MICROSCOPY": 3,
    }
    with sqlite3.connect(BACKEND_ROOT / "instance" / "health_system.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM report_assets WHERE asset_type_id IS NULL"
        ).fetchone()[0] == 0
        assert connection.execute("""
            SELECT COUNT(*)
            FROM report_assets AS a
            JOIN report_asset_types AS t ON t.id = a.asset_type_id
            WHERE a.health_domain_id != t.health_domain_id
        """).fetchone()[0] == 0
        assert connection.execute("""
            SELECT COUNT(*)
            FROM report_assets AS a
            JOIN institution_reports AS r ON r.id = a.report_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM package_version_domains AS pvd
                WHERE pvd.package_version_id = r.package_version_id
                  AND pvd.health_domain_id = a.health_domain_id
            )
        """).fetchone()[0] == 0
        rows = connection.execute("""
            SELECT
                a.storage_key, a.title, a.mime_type, a.byte_size,
                a.width, a.height, a.sha256, t.code
            FROM report_assets AS a
            JOIN report_asset_types AS t ON t.id = a.asset_type_id
        """).fetchall()
        database_assets = {
            row[0]: {
                "title": row[1],
                "mime_type": row[2],
                "byte_size": row[3],
                "width": row[4],
                "height": row[5],
                "sha256": row[6],
                "asset_type_code": row[7],
            }
            for row in rows
        }
    manifest_assets = {
        item["storage_key"]: {
            field: item[field]
            for field in (
                "title", "mime_type", "byte_size", "width", "height",
                "sha256", "asset_type_code",
            )
        }
        for item in report_items
    }
    assert database_assets == manifest_assets


def test_demo_media_refresh_adds_missing_standard_asset_type(tmp_path):
    database = tmp_path / "older-v10-server.db"
    shutil.copy2(BACKEND_ROOT / "instance" / "health_system.db", database)
    with sqlite3.connect(database) as connection:
        blood_type = connection.execute(
            "SELECT id FROM report_asset_types WHERE code = 'BLOOD_MICROSCOPY'"
        ).fetchone()[0]
        other_type, other_domain = connection.execute("""
            SELECT id, health_domain_id
            FROM report_asset_types
            WHERE code = 'OTHER'
        """).fetchone()
        connection.execute("""
            UPDATE report_assets
            SET asset_type_id = ?, health_domain_id = ?
            WHERE asset_type_id = ?
        """, (other_type, other_domain, blood_type))
        connection.execute(
            "DELETE FROM report_asset_types WHERE id = ?",
            (blood_type,),
        )
        connection.commit()

    items = validate_demo_media(BACKEND_ROOT / "uploads")
    apply_demo_media(f"sqlite:///{database.as_posix()}", items)

    with sqlite3.connect(database) as connection:
        assert connection.execute("""
            SELECT COUNT(*)
            FROM report_asset_types
            WHERE code = 'BLOOD_MICROSCOPY'
              AND is_active = 1
        """).fetchone()[0] == 1
        assert connection.execute("""
            SELECT COUNT(*)
            FROM report_assets AS a
            JOIN report_asset_types AS t ON t.id = a.asset_type_id
            WHERE t.code = 'BLOOD_MICROSCOPY'
        """).fetchone()[0] == 3


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
