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
    return {"Authorization": f"Bearer {access_token}"}


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


def test_comment_requires_uploaded_record(client):
    user_headers = _auth_headers(client, "comment_user_a")
    institution_id, _ = _first_institution_and_package(client, user_headers)

    response = client.post(
        "/api/comments",
        headers=user_headers,
        json={"institution_id": institution_id, "content": "服务不错", "rating": 5},
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "comment_requires_record"
    assert response.get_json()["message"] == "upload a record for this institution before commenting"


def test_comment_moderation_flow(client, app):
    user_headers = _auth_headers(client, "comment_user_b")
    admin_headers = _auth_headers(client, "comment_admin")
    institution_id, package_id = _first_institution_and_package(client, user_headers)

    with app.app_context():
        admin = User.query.filter_by(username="comment_admin").first()
        admin.role = "admin"
        db.session.commit()

    _create_record(client, user_headers, institution_id, package_id)

    create_response = client.post(
        "/api/comments",
        headers=user_headers,
        json={"institution_id": institution_id, "content": "流程很顺畅，体验好", "rating": 4},
    )
    assert create_response.status_code == 201
    comment_id = create_response.get_json()["item"]["id"]
    assert create_response.get_json()["item"]["is_visible"] is False

    public_before_visible = client.get(
        f"/api/comments?institution_id={institution_id}",
        headers=user_headers,
    )
    assert public_before_visible.status_code == 200
    assert all(item["id"] != comment_id for item in public_before_visible.get_json()["items"])

    moderation_list = client.get("/api/comments/moderation", headers=admin_headers)
    assert moderation_list.status_code == 200
    assert any(item["id"] == comment_id for item in moderation_list.get_json()["items"])

    visibility_response = client.put(
        f"/api/comments/{comment_id}/visibility",
        headers=admin_headers,
        json={"is_visible": True},
    )
    assert visibility_response.status_code == 200
    assert visibility_response.get_json()["item"]["is_visible"] is True

    public_after_visible = client.get(
        f"/api/comments?institution_id={institution_id}",
        headers=user_headers,
    )
    assert any(item["id"] == comment_id for item in public_after_visible.get_json()["items"])

    delete_response = client.delete(f"/api/comments/{comment_id}", headers=admin_headers)
    assert delete_response.status_code == 200

    moderation_after_delete = client.get("/api/comments/moderation", headers=admin_headers)
    assert all(item["id"] != comment_id for item in moderation_after_delete.get_json()["items"])

    non_admin_moderation = client.get("/api/comments/moderation", headers=user_headers)
    assert non_admin_moderation.status_code == 403


def test_user_can_list_and_delete_own_comments(client):
    user_headers = _auth_headers(client, "comment_user_c")
    other_headers = _auth_headers(client, "comment_user_d")
    institution_id, package_id = _first_institution_and_package(client, user_headers)
    _create_record(client, user_headers, institution_id, package_id)

    create_response = client.post(
        "/api/comments",
        headers=user_headers,
        json={"institution_id": institution_id, "content": "我自己的评论", "rating": 5},
    )
    assert create_response.status_code == 201
    comment_id = create_response.get_json()["item"]["id"]
    assert create_response.get_json()["item"]["is_visible"] is False

    my_comments = client.get("/api/comments/mine", headers=user_headers)
    assert my_comments.status_code == 200
    assert any(item["id"] == comment_id for item in my_comments.get_json()["items"])

    my_comments_by_institution = client.get(
        f"/api/comments/mine?institution_id={institution_id}",
        headers=user_headers,
    )
    assert my_comments_by_institution.status_code == 200
    assert any(item["id"] == comment_id for item in my_comments_by_institution.get_json()["items"])

    delete_forbidden = client.delete(f"/api/comments/{comment_id}", headers=other_headers)
    assert delete_forbidden.status_code == 403

    delete_success = client.delete(f"/api/comments/{comment_id}", headers=user_headers)
    assert delete_success.status_code == 200

    my_comments_after_delete = client.get("/api/comments/mine", headers=user_headers)
    assert all(item["id"] != comment_id for item in my_comments_after_delete.get_json()["items"])
