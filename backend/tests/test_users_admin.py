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
    user_id = login_response.get_json()["user"]["id"]
    return {"Authorization": f"Bearer {access_token}"}, user_id


def _first_institution_and_package(client, headers):
    institutions = client.get("/api/institutions", headers=headers).get_json()["items"]
    institution_id = institutions[0]["id"]
    packages = client.get(f"/api/institutions/{institution_id}/packages", headers=headers).get_json()["items"]
    package_id = packages[0]["id"]
    return institution_id, package_id


def _create_record(client, headers, institution_id, package_id):
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
    return response.get_json()["item"]["id"]


def test_admin_can_update_user(client, app):
    admin_headers, _ = _auth_headers(client, "user_admin_updater")
    _, user_id = _auth_headers(client, "user_target_update")
    non_admin_headers, _ = _auth_headers(client, "user_non_admin_update")

    with app.app_context():
        admin = User.query.filter_by(username="user_admin_updater").first()
        admin.role = "admin"
        db.session.commit()

    update_response = client.put(
        f"/api/users/{user_id}",
        headers=admin_headers,
        json={
            "username": "user_target_updated",
            "email": "target_updated@example.com",
            "phone": "13800138000",
            "role": "admin",
        },
    )
    assert update_response.status_code == 200
    item = update_response.get_json()["item"]
    assert item["username"] == "user_target_updated"
    assert item["email"] == "target_updated@example.com"
    assert item["phone"] == "13800138000"
    assert item["role"] == "admin"

    forbidden_update = client.put(
        f"/api/users/{user_id}",
        headers=non_admin_headers,
        json={"phone": "10086"},
    )
    assert forbidden_update.status_code == 403


def test_admin_can_delete_user_and_related_data(client, app):
    admin_headers, admin_id = _auth_headers(client, "user_admin_deleter")
    user_headers, user_id = _auth_headers(client, "user_target_delete")

    with app.app_context():
        admin = User.query.filter_by(username="user_admin_deleter").first()
        admin.role = "admin"
        db.session.commit()

    institution_id, package_id = _first_institution_and_package(client, user_headers)
    record_id = _create_record(client, user_headers, institution_id, package_id)

    comment_response = client.post(
        "/api/comments",
        headers=user_headers,
        json={"institution_id": institution_id, "content": "将被删除的评论", "rating": 4},
    )
    assert comment_response.status_code == 201
    comment_id = comment_response.get_json()["item"]["id"]

    delete_response = client.delete(f"/api/users/{user_id}", headers=admin_headers)
    assert delete_response.status_code == 200

    users_after_delete = client.get("/api/users", headers=admin_headers)
    assert all(item["id"] != user_id for item in users_after_delete.get_json()["items"])

    login_after_delete = client.post(
        "/api/auth/login",
        json=client.login_payload("user_target_delete"),
    )
    assert login_after_delete.status_code == 401

    records_after_delete = client.get("/api/records", headers=admin_headers)
    assert all(item["id"] != record_id for item in records_after_delete.get_json()["items"])

    comments_after_delete = client.get("/api/comments/moderation", headers=admin_headers)
    assert all(item["id"] != comment_id for item in comments_after_delete.get_json()["items"])

    delete_self = client.delete(f"/api/users/{admin_id}", headers=admin_headers)
    assert delete_self.status_code == 400
