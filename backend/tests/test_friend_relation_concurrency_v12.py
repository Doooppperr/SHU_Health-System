from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier

from flask_jwt_extended import create_access_token

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import FriendRelation, User


def _user(username, health_id, real_name):
    user = User(
        username=username,
        email=f"{username}@example.test",
        role="user",
        health_id=health_id,
        real_name=real_name,
        gender="undisclosed",
        birth_date=date(1990, 1, 1),
        identity_completed_at=datetime.now(timezone.utc),
        is_active=True,
    )
    user.set_password("friend-race-password")
    return user


def test_pending_accept_and_revoke_use_one_atomic_relationship_revision(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "friend-relation-cas.db"
    monkeypatch.setattr(
        TestingConfig,
        "SQLALCHEMY_DATABASE_URI",
        f"sqlite:///{database_path.as_posix()}",
    )
    concurrent_app = create_app("testing")

    with concurrent_app.app_context():
        requester = _user(
            "friend_race_requester",
            "HID-23456789",
            "虚构申请人",
        )
        recipient = _user(
            "friend_race_recipient",
            "HID-ABCDEFGH",
            "虚构接收人",
        )
        db.session.add_all([requester, recipient])
        db.session.flush()
        relation = FriendRelation(
            user_id=requester.id,
            friend_user_id=recipient.id,
            pair_key=FriendRelation.canonical_pair_key(
                requester.id,
                recipient.id,
            ),
            relation_name="虚构亲友",
            status="pending",
            auth_status=False,
            reverse_auth_status=False,
            booking_auth_status=False,
            reverse_booking_auth_status=False,
        )
        db.session.add(relation)
        db.session.commit()
        relation_id = relation.id
        requester_token = create_access_token(
            identity=str(requester.id),
            additional_claims={
                "role": "user",
                "token_version": requester.token_version,
            },
        )
        recipient_token = create_access_token(
            identity=str(recipient.id),
            additional_claims={
                "role": "user",
                "token_version": recipient.token_version,
            },
        )

    from app.friends import routes as friend_routes

    original_activate = friend_routes._activate_relation_cas
    original_revoke = friend_routes._revoke_relation_cas
    transition_barrier = Barrier(2)

    def synchronized_activate(relation, user_id, when):
        transition_barrier.wait(timeout=10)
        return original_activate(relation, user_id, when)

    def synchronized_revoke(relation, user_id, when):
        transition_barrier.wait(timeout=10)
        return original_revoke(relation, user_id, when)

    monkeypatch.setattr(
        friend_routes,
        "_activate_relation_cas",
        synchronized_activate,
    )
    monkeypatch.setattr(
        friend_routes,
        "_revoke_relation_cas",
        synchronized_revoke,
    )

    def accept():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/friends/{relation_id}/accept",
                headers={"Authorization": f"Bearer {recipient_token}"},
            )
            return response.status_code, response.get_json()

    def revoke():
        with concurrent_app.test_client() as worker_client:
            response = worker_client.delete(
                f"/api/friends/{relation_id}",
                headers={"Authorization": f"Bearer {requester_token}"},
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        accept_future = executor.submit(accept)
        revoke_future = executor.submit(revoke)
        accept_result = accept_future.result(timeout=20)
        revoke_result = revoke_future.result(timeout=20)

    assert sorted((accept_result[0], revoke_result[0])) == [200, 409]
    conflict_payload = (
        accept_result[1] if accept_result[0] == 409 else revoke_result[1]
    )
    assert conflict_payload["code"] == "RELATIONSHIP_STATE_CONFLICT"

    with concurrent_app.app_context():
        final_relation = db.session.get(FriendRelation, relation_id)
        assert final_relation.authorization_version == 1
        assert final_relation.booking_authorization_version == 1
        if revoke_result[0] == 200:
            assert final_relation.status == "revoked"
            assert final_relation.is_active is False
        else:
            assert accept_result[0] == 200
            assert final_relation.status == "active"
            assert final_relation.is_active is True
