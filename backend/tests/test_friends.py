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


def test_friends_add_list_rename_authorize_and_delete(client):
    alice_headers = _auth_headers(client, "friend_alice")
    bob_headers = _auth_headers(client, "friend_bob")

    add_response = client.post(
        "/api/friends",
        headers=alice_headers,
        json={"friend_username": "friend_bob", "relation_name": "同学"},
    )
    assert add_response.status_code == 201
    relation_item = add_response.get_json()["item"]
    relation_id = relation_item["id"]
    assert relation_item["auth_status"] is False

    alice_list = client.get("/api/friends", headers=alice_headers)
    assert alice_list.status_code == 200
    assert any(item["id"] == relation_id for item in alice_list.get_json()["outgoing"])

    bob_list = client.get("/api/friends", headers=bob_headers)
    assert bob_list.status_code == 200
    assert any(item["id"] == relation_id for item in bob_list.get_json()["incoming"])

    alice_toggle_forbidden = client.put(
        f"/api/friends/{relation_id}/authorization",
        headers=alice_headers,
        json={"auth_status": True},
    )
    assert alice_toggle_forbidden.status_code == 403

    bob_toggle = client.put(
        f"/api/friends/{relation_id}/authorization",
        headers=bob_headers,
        json={"auth_status": True},
    )
    assert bob_toggle.status_code == 200
    assert bob_toggle.get_json()["item"]["auth_status"] is True

    rename_by_alice = client.put(
        f"/api/friends/{relation_id}",
        headers=alice_headers,
        json={"relation_name": "家人"},
    )
    assert rename_by_alice.status_code == 200
    assert rename_by_alice.get_json()["item"]["relation_name"] == "家人"

    rename_by_bob = client.put(
        f"/api/friends/{relation_id}",
        headers=bob_headers,
        json={"relation_name": "不应成功"},
    )
    assert rename_by_bob.status_code == 403

    duplicate_add = client.post(
        "/api/friends",
        headers=alice_headers,
        json={"friend_username": "friend_bob", "relation_name": "同学"},
    )
    assert duplicate_add.status_code == 409

    delete_response = client.delete(f"/api/friends/{relation_id}", headers=bob_headers)
    assert delete_response.status_code == 200

    alice_list_after_delete = client.get("/api/friends", headers=alice_headers)
    assert all(item["id"] != relation_id for item in alice_list_after_delete.get_json()["outgoing"])
