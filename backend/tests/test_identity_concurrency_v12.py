from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from flask_jwt_extended import create_access_token

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import User


def test_identity_completion_is_atomic_and_immutable_under_concurrency(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "identity-completion-cas.db"
    monkeypatch.setattr(
        TestingConfig,
        "SQLALCHEMY_DATABASE_URI",
        f"sqlite:///{database_path.as_posix()}",
    )
    concurrent_app = create_app("testing")
    with concurrent_app.app_context():
        user = User(
            username="identity_race_user",
            email="identity-race@example.test",
            role="user",
            health_id="HID-ZZZZZZZZ",
            is_active=True,
        )
        user.set_password("identity-race-password")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        token = create_access_token(
            identity=str(user.id),
            additional_claims={
                "role": "user",
                "token_version": user.token_version,
            },
        )

    from app.profile import routes as profile_routes

    original_complete = profile_routes._complete_identity_cas
    completion_barrier = Barrier(2)

    def synchronized_complete(*args, **kwargs):
        completion_barrier.wait(timeout=10)
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(
        profile_routes,
        "_complete_identity_cas",
        synchronized_complete,
    )
    candidates = (
        {
            "real_name": "虚构身份甲",
            "gender": "female",
            "birth_date": "1991-02-03",
        },
        {
            "real_name": "虚构身份乙",
            "gender": "male",
            "birth_date": "1984-05-06",
        },
    )

    def complete(payload):
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                "/api/profile/me/complete",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            return response.status_code, response.get_json(), payload

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=20)
            for future in (
                executor.submit(complete, candidates[0]),
                executor.submit(complete, candidates[1]),
            )
        ]

    assert sorted(status for status, _body, _payload in results) == [200, 409]
    loser = next(body for status, body, _payload in results if status == 409)
    assert loser["code"] == "PROFILE_ALREADY_COMPLETED"
    winner = next(payload for status, _body, payload in results if status == 200)

    with concurrent_app.app_context():
        stored = db.session.get(User, user_id)
        assert stored.identity_completed_at is not None
        assert stored.real_name == winner["real_name"]
        assert stored.gender == winner["gender"]
        assert stored.birth_date.isoformat() == winner["birth_date"]
