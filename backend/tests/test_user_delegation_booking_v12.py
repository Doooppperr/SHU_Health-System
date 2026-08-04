from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    Appointment,
    BookingParticipantToken,
    DelegatedActionAudit,
    DelegationSessionAudit,
    FriendRelation,
    HealthDomain,
    IndicatorDict,
    Institution,
    InstitutionReport,
    NotificationOutbox,
    Package,
    SelfMeasurement,
    User,
    UserNotification,
    WaitlistSubscription,
    WaitlistSubscriptionParticipant,
)
from app.services.booking_participants import resolve_booking_participants


def _login(client, username, password="Shuhealthdoc！"):
    response = client.post(
        "/api/auth/login",
        json=client.login_payload(username, password),
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    return {
        "Authorization": f"Bearer {payload['access_token']}",
    }, payload


def _register(client, username):
    response = client.post(
        "/api/auth/register",
        json=client.register_payload(
            username,
            email=f"{username}@example.test",
        ),
    )
    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
    return {
        "Authorization": f"Bearer {payload['access_token']}",
    }, payload


def _complete(client, headers, name, *, gender="female", birth_date="1990-05-06"):
    response = client.post(
        "/api/profile/me/complete",
        headers=headers,
        json={
            "real_name": name,
            "gender": gender,
            "birth_date": birth_date,
        },
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()["item"]


def _package_for(institution):
    return (
        Package.query.filter_by(
            institution_id=institution.id,
            is_active=True,
        )
        .order_by(Package.id)
        .first()
    )


def test_delegation_never_upgrades_a_stale_source_token_epoch(app):
    from app.services.delegation import start_delegation

    with app.app_context():
        actor = User.query.filter_by(username="test1").one()
        relation = FriendRelation.query.filter(
            db.or_(
                FriendRelation.user_id == actor.id,
                FriendRelation.friend_user_id == actor.id,
            )
        ).first()
        assert relation is not None
        if not relation.is_active:
            relation.activate()
        source_version = actor.token_version
        source_claims = {
            "sub": str(actor.id),
            "role": "user",
            "token_version": source_version,
        }
        actor.token_version += 1
        db.session.commit()

        result, error = start_delegation(actor, relation, source_claims)
        assert result is None
        payload, status = error
        assert status == 401
        assert payload["code"] == "TOKEN_REVOKED"


def test_identity_is_atomic_immutable_and_guards_health_writes(client):
    headers, _payload = _register(client, "identity_v12")
    blocked = client.post(
        "/api/self-measurements",
        headers=headers,
        json={},
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["code"] == "IDENTITY_REQUIRED"

    item = _complete(client, headers, "林岚")
    assert item["identity_completed"] is True
    assert item["identity_completed_at"]
    assert item["profile_completed"] is True
    assert item["profile_completed_at"]

    repeated = client.post(
        "/api/profile/me/complete",
        headers=headers,
        json={
            "real_name": "另一个姓名",
            "gender": "female",
            "birth_date": "1991-01-01",
        },
    )
    assert repeated.status_code == 409
    assert repeated.get_json()["code"] == "PROFILE_ALREADY_COMPLETED"

    changed = client.put(
        "/api/profile/me",
        headers=headers,
        json={"real_name": "另一个姓名"},
    )
    assert changed.status_code == 409
    assert changed.get_json()["code"] == "PROFILE_IDENTITY_IMMUTABLE"


def test_identity_boundary_blocks_business_posts_but_allows_account_recovery(
    client,
):
    headers, _payload = _register(client, "identity_boundary_v12")

    # Read-only notification access remains available so a newly registered
    # user can still see platform guidance.
    listed = client.get("/api/notifications", headers=headers)
    assert listed.status_code == 200

    for path, payload in (
        ("/api/notifications/read-all", None),
        ("/api/ai/chat", {"message": "分析我的健康数据"}),
    ):
        blocked = client.post(path, headers=headers, json=payload)
        assert blocked.status_code == 409
        assert blocked.get_json()["code"] == "IDENTITY_REQUIRED"

    # Account-security and recovery operations are deliberately allowlisted.
    password_code = client.post(
        "/api/auth/password-change/code",
        headers=headers,
    )
    assert password_code.status_code == 200, password_code.get_json()
    challenge = password_code.get_json()
    changed_email = client.put(
        "/api/auth/email",
        headers=headers,
        json={
            "email": "identity-boundary-updated@example.test",
            "current_password": "secret123",
            "challenge_id": challenge["challenge_id"],
            "verification_code": challenge["verification_code"],
        },
    )
    assert changed_email.status_code == 200, changed_email.get_json()


def test_bidirectional_friend_switch_is_audited_and_exit_revokes_chain(
    app,
    client,
):
    test1_headers, _ = _login(client, "test1")
    test2_headers, _ = _login(client, "test2")
    with app.app_context():
        test1 = User.query.filter_by(username="test1").one()
        test2 = User.query.filter_by(username="test2").one()
        relation = FriendRelation.query.filter_by(
            user_id=test1.id,
            friend_user_id=test2.id,
        ).one()
        relation_id = relation.id
        subject_id = test2.id

    granted = client.put(
        f"/api/friends/{relation_id}/authorization",
        headers=test2_headers,
        json={"health_view_granted_by_me": True},
    )
    assert granted.status_code == 200, granted.get_json()
    assert granted.get_json()["item"]["health_view_granted_by_me"] is True

    switched = client.post(
        f"/api/friends/{relation_id}/switch-session",
        headers=test1_headers,
    )
    assert switched.status_code == 200, switched.get_json()
    payload = switched.get_json()
    delegated_headers = {
        "Authorization": f"Bearer {payload['access_token']}",
    }
    assert payload["session"]["delegated"] is True
    assert payload["session"]["depth"] == 1

    subject = client.get("/api/users/me", headers=delegated_headers)
    assert subject.status_code == 200
    assert subject.get_json()["user"]["username"] == "test2"

    # Delegated sessions are full subject sessions except for account
    # deactivation; this write is attributed through the audit metadata.
    profile_write = client.put(
        "/api/profile/me",
        headers=delegated_headers,
        json={"phone": "13900000002"},
    )
    assert profile_write.status_code == 200, profile_write.get_json()

    # The request audit is captured before the global identity gate so even a
    # rejected delegated mutation keeps actor/subject attribution.
    with app.app_context():
        subject_user = db.session.get(User, subject_id)
        completed_at = subject_user.identity_completed_at
        subject_user.identity_completed_at = None
        db.session.commit()
    guarded = client.post(
        "/api/notifications/read-all",
        headers=delegated_headers,
    )
    assert guarded.status_code == 409
    assert guarded.get_json()["code"] == "IDENTITY_REQUIRED"
    with app.app_context():
        db.session.get(User, subject_id).identity_completed_at = completed_at
        db.session.commit()

    exited = client.post(
        "/api/auth/delegation/exit",
        headers=delegated_headers,
    )
    assert exited.status_code == 200
    assert exited.get_json()["redirect_to"] == "/login"
    assert "access_token" not in exited.get_json()

    revoked = client.get("/api/users/me", headers=delegated_headers)
    assert revoked.status_code == 401
    assert client.get("/api/users/me", headers=test1_headers).status_code == 401
    with app.app_context():
        paths = {
            row.path
            for row in DelegatedActionAudit.query.order_by(
                DelegatedActionAudit.id
            ).all()
        }
        assert "/api/users/me" in paths
        assert "/api/profile/me" in paths
        guarded_audit = DelegatedActionAudit.query.filter_by(
            path="/api/notifications/read-all",
            status_code=409,
        ).one()
        assert guarded_audit.subject_user_id == subject_id
        assert guarded_audit.outcome == "error"
        assert "/api/auth/delegation/exit" in paths


def test_delegation_can_switch_repeatedly_in_both_directions(app, client):
    actor_headers, _ = _login(client, "test1")
    with app.app_context():
        test1 = User.query.filter_by(username="test1").one()
        test2 = User.query.filter_by(username="test2").one()
        test4 = User.query.filter_by(username="test4").one()
        first_relation = FriendRelation.query.filter(
            db.or_(
                db.and_(
                    FriendRelation.user_id == test1.id,
                    FriendRelation.friend_user_id == test2.id,
                ),
                db.and_(
                    FriendRelation.user_id == test2.id,
                    FriendRelation.friend_user_id == test1.id,
                ),
            )
        ).one()
        second_relation = FriendRelation.query.filter(
            db.or_(
                db.and_(
                    FriendRelation.user_id == test2.id,
                    FriendRelation.friend_user_id == test4.id,
                ),
                db.and_(
                    FriendRelation.user_id == test4.id,
                    FriendRelation.friend_user_id == test2.id,
                ),
            )
        ).one()
        first_relation.activate()
        second_relation.activate()
        first_relation_id = first_relation.id
        second_relation_id = second_relation.id
        actor_id = test1.id
        db.session.commit()

    first = client.post(
        f"/api/friends/{first_relation_id}/switch-session",
        headers=actor_headers,
    )
    assert first.status_code == 200, first.get_json()
    first_headers = {
        "Authorization": f"Bearer {first.get_json()['access_token']}"
    }
    second = client.post(
        f"/api/friends/{second_relation_id}/switch-session",
        headers=first_headers,
    )
    assert second.status_code == 200, second.get_json()
    second_headers = {
        "Authorization": f"Bearer {second.get_json()['access_token']}"
    }

    returned_to_test2 = client.post(
        f"/api/friends/{second_relation_id}/switch-session",
        headers=second_headers,
    )
    assert returned_to_test2.status_code == 200, returned_to_test2.get_json()
    returned_payload = returned_to_test2.get_json()
    assert returned_payload["session"]["depth"] == 1
    assert returned_payload["session"]["relation_id"] == first_relation_id
    returned_test2_headers = {
        "Authorization": f"Bearer {returned_payload['access_token']}"
    }
    assert client.get(
        "/api/users/me",
        headers=returned_test2_headers,
    ).get_json()["user"]["username"] == "test2"
    assert client.get("/api/users/me", headers=second_headers).status_code == 401

    returned_to_test1 = client.post(
        f"/api/friends/{first_relation_id}/switch-session",
        headers=returned_test2_headers,
    )
    assert returned_to_test1.status_code == 200, returned_to_test1.get_json()
    assert returned_to_test1.get_json()["session"] is None
    returned_test1_headers = {
        "Authorization": f"Bearer {returned_to_test1.get_json()['access_token']}"
    }
    assert client.get(
        "/api/users/me",
        headers=returned_test1_headers,
    ).get_json()["user"]["username"] == "test1"
    assert client.get("/api/users/me", headers=first_headers).status_code == 401

    switched_again = client.post(
        f"/api/friends/{first_relation_id}/switch-session",
        headers=returned_test1_headers,
    )
    assert switched_again.status_code == 200, switched_again.get_json()
    assert switched_again.get_json()["session"]["depth"] == 1
    switched_again_headers = {
        "Authorization": f"Bearer {switched_again.get_json()['access_token']}"
    }
    assert client.get(
        "/api/users/me",
        headers=switched_again_headers,
    ).get_json()["user"]["username"] == "test2"

    with app.app_context():
        sessions = DelegationSessionAudit.query.filter_by(
            actor_user_id=actor_id,
        ).all()
        assert len(sessions) == 3
        assert sum(row.status == "exited" for row in sessions) == 2
        assert sum(row.status == "active" for row in sessions) == 1


def test_friend_request_accepts_both_directions_and_any_side_revokes(
    app,
    client,
):
    requester_headers, requester_registration = _register(
        client,
        "friend_requester_v12",
    )
    target_headers, target_registration = _register(
        client,
        "friend_target_v12",
    )
    requester = _complete(
        client,
        requester_headers,
        "许安",
        gender="male",
    )
    target = _complete(client, target_headers, "宋遥")

    created = client.post(
        "/api/friends",
        headers=requester_headers,
        json={
            "health_id": target["health_id"],
            "relation_name": "家人",
        },
    )
    assert created.status_code == 201, created.get_json()
    relation_id = created.get_json()["item"]["id"]
    pending_item = created.get_json()["item"]
    assert pending_item["relationship_status"] == "pending"
    assert pending_item["counterparty"]["display_name"] == "宋*"
    assert pending_item["counterparty"]["display_name"] != "宋遥"

    cannot_self_accept = client.put(
        f"/api/friends/{relation_id}/authorization",
        headers=requester_headers,
        json={"auth_status": True},
    )
    assert cannot_self_accept.status_code == 403
    assert (
        cannot_self_accept.get_json()["code"]
        == "FRIEND_REQUEST_ACCEPT_FORBIDDEN"
    )

    # The old booking toggle endpoint maps to the same all-or-nothing
    # relationship acceptance and cannot produce a half-authorized row.
    accepted = client.put(
        f"/api/friends/{relation_id}/booking-authorization",
        headers=target_headers,
        json={"booking_auth_status": True},
    )
    assert accepted.status_code == 200, accepted.get_json()
    item = accepted.get_json()["item"]
    assert item["relationship_status"] == "active"
    assert item["counterparty"]["display_name"] == "许安"
    assert item["health_view_granted_to_me"] is True
    assert item["health_view_granted_by_me"] is True
    assert item["booking_granted_to_me"] is True
    assert item["booking_granted_by_me"] is True
    with app.app_context():
        relation = db.session.get(FriendRelation, relation_id)
        assert relation.is_active
        assert {
            relation.auth_status,
            relation.reverse_auth_status,
            relation.booking_auth_status,
            relation.reverse_booking_auth_status,
        } == {True}

    revoked = client.put(
        f"/api/friends/{relation_id}/authorization",
        headers=target_headers,
        json={"auth_status": False},
    )
    assert revoked.status_code == 200
    revoked_item = revoked.get_json()["item"]
    assert revoked_item["relationship_status"] == "revoked"
    assert revoked_item["counterparty"]["display_name"] == "许*"
    with app.app_context():
        relation = db.session.get(FriendRelation, relation_id)
        assert relation.status == "revoked"
        assert relation.revoked_at is not None
        assert not relation.is_active

    recreated = client.post(
        "/api/friends",
        headers=requester_headers,
        json={
            "health_id": target["health_id"],
            "relation_name": "家人",
        },
    )
    assert recreated.status_code == 201
    assert recreated.get_json()["item"]["id"] == relation_id
    assert recreated.get_json()["item"]["relationship_status"] == "pending"
    assert recreated.get_json()["item"]["counterparty"]["display_name"] == "宋*"


def test_delegation_exit_revokes_sibling_and_downstream_sessions(app, client):
    actor_headers, _ = _login(client, "test1")
    with app.app_context():
        test1 = User.query.filter_by(username="test1").one()
        test2 = User.query.filter_by(username="test2").one()
        test4 = User.query.filter_by(username="test4").one()
        first_relation = FriendRelation.query.filter(
            db.or_(
                db.and_(
                    FriendRelation.user_id == test1.id,
                    FriendRelation.friend_user_id == test2.id,
                ),
                db.and_(
                    FriendRelation.user_id == test2.id,
                    FriendRelation.friend_user_id == test1.id,
                ),
            )
        ).one()
        downstream_relation = FriendRelation.query.filter(
            db.or_(
                db.and_(
                    FriendRelation.user_id == test2.id,
                    FriendRelation.friend_user_id == test4.id,
                ),
                db.and_(
                    FriendRelation.user_id == test4.id,
                    FriendRelation.friend_user_id == test2.id,
                ),
            )
        ).one()
        first_relation.activate()
        downstream_relation.activate()
        first_relation_id = first_relation.id
        downstream_relation_id = downstream_relation.id
        actor_id = test1.id
        db.session.commit()

    first = client.post(
        f"/api/friends/{first_relation_id}/switch-session",
        headers=actor_headers,
    ).get_json()
    sibling = client.post(
        f"/api/friends/{first_relation_id}/switch-session",
        headers=actor_headers,
    ).get_json()
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    sibling_headers = {"Authorization": f"Bearer {sibling['access_token']}"}
    downstream = client.post(
        f"/api/friends/{downstream_relation_id}/switch-session",
        headers=first_headers,
    )
    assert downstream.status_code == 200, downstream.get_json()
    downstream_headers = {
        "Authorization": f"Bearer {downstream.get_json()['access_token']}"
    }

    exited = client.post(
        "/api/auth/delegation/exit",
        headers=first_headers,
    )
    assert exited.status_code == 200, exited.get_json()
    for stale_headers in (first_headers, sibling_headers, downstream_headers):
        assert client.get("/api/users/me", headers=stale_headers).status_code == 401
    assert client.get("/api/users/me", headers=actor_headers).status_code == 401

    with app.app_context():
        sessions = DelegationSessionAudit.query.filter_by(
            actor_user_id=actor_id,
        ).all()
        assert len(sessions) == 3
        assert {row.status for row in sessions} == {"exited"}


def test_normal_logout_revokes_access_refresh_and_delegations(client):
    headers, payload = _login(client, "test3")
    logged_out = client.post("/api/auth/logout", headers=headers)
    assert logged_out.status_code == 200, logged_out.get_json()
    assert client.get("/api/users/me", headers=headers).status_code == 401
    assert client.post(
        "/api/auth/refresh",
        headers={"Authorization": f"Bearer {payload['refresh_token']}"},
    ).status_code == 401


def test_admin_active_state_cycle_never_revives_jwt_delegation_or_bpt(
    app,
    client,
):
    actor_headers, actor_login = _register(client, "state_cycle_actor_v12")
    subject_headers, _ = _register(client, "state_cycle_subject_v12")
    token_target_headers, _ = _register(client, "state_cycle_token_target_v12")
    actor_profile = _complete(client, actor_headers, "测试启停操作者")
    subject_profile = _complete(client, subject_headers, "测试关联对象")
    token_target = _complete(client, token_target_headers, "测试代约对象")

    requested = client.post(
        "/api/friends",
        headers=actor_headers,
        json={
            "health_id": subject_profile["health_id"],
            "relation_name": "测试关联",
        },
    )
    assert requested.status_code == 201, requested.get_json()
    relation_id = requested.get_json()["item"]["id"]
    accepted = client.put(
        f"/api/friends/{relation_id}/authorization",
        headers=subject_headers,
        json={"auth_status": True},
    )
    assert accepted.status_code == 200, accepted.get_json()
    switched = client.post(
        f"/api/friends/{relation_id}/switch-session",
        headers=actor_headers,
    )
    assert switched.status_code == 200, switched.get_json()
    delegated_headers = {
        "Authorization": f"Bearer {switched.get_json()['access_token']}"
    }

    issued = client.post(
        "/api/booking-participants/resolve",
        headers=actor_headers,
        json={"health_id": token_target["health_id"]},
    )
    assert issued.status_code == 200, issued.get_json()
    raw_participant_token = issued.get_json()["item"]["participant_token"]

    with app.app_context():
        actor = User.query.filter_by(username="state_cycle_actor_v12").one()
        actor_id = actor.id
        initial_token_version = actor.token_version
        initial_booking_version = actor.booking_authorization_version

    admin_headers, _ = _login(client, "admin", "admin123")
    invalid_type = client.put(
        f"/api/users/{actor_id}",
        headers=admin_headers,
        json={"is_active": "false"},
    )
    assert invalid_type.status_code == 400

    for active in (False, True):
        changed = client.put(
            f"/api/users/{actor_id}",
            headers=admin_headers,
            json={"is_active": active},
        )
        assert changed.status_code == 200, changed.get_json()

    for stale_headers in (actor_headers, delegated_headers):
        assert client.get("/api/users/me", headers=stale_headers).status_code == 401
    assert client.post(
        "/api/auth/refresh",
        headers={
            "Authorization": f"Bearer {actor_login['refresh_token']}"
        },
    ).status_code == 401

    with app.app_context():
        actor = db.session.get(User, actor_id)
        assert actor.is_active is True
        assert actor.token_version == initial_token_version + 2
        assert (
            actor.booking_authorization_version
            == initial_booking_version + 2
        )
        session = DelegationSessionAudit.query.filter_by(
            actor_user_id=actor_id,
        ).one()
        assert session.status == "revoked"
        token_row = BookingParticipantToken.query.filter_by(
            booker_user_id=actor_id,
        ).one()
        assert token_row.revoked_at is not None
        participants, error = resolve_booking_participants(
            actor,
            {
                "participants": [{
                    "type": "health_code_token",
                    "participant_token": raw_participant_token,
                }]
            },
        )
        assert participants is None
        assert error[0]["code"] == "PARTICIPANT_TOKEN_EXPIRED"


def test_health_reads_require_switching_to_the_effective_account(app, client):
    headers, _ = _login(client, "test1")
    with app.app_context():
        current = User.query.filter_by(username="test1").one()
        linked = User.query.filter_by(username="test2").one()
        relation = FriendRelation.query.filter(
            db.or_(
                db.and_(
                    FriendRelation.user_id == current.id,
                    FriendRelation.friend_user_id == linked.id,
                ),
                db.and_(
                    FriendRelation.user_id == linked.id,
                    FriendRelation.friend_user_id == current.id,
                ),
            )
        ).one()
        relation.activate()
        linked_report = InstitutionReport.query.filter_by(
            matched_user_id=linked.id,
            status="published",
        ).first()
        assert linked_report is not None
        domain = HealthDomain.query.filter_by(is_active=True).first()
        indicator = IndicatorDict.query.first()
        current_id = current.id
        linked_id = linked.id
        linked_report_id = linked_report.id
        domain_id = domain.id
        indicator_id = indicator.id
        db.session.commit()

    for path in (
        f"/api/health/timeline?owner_id={linked_id}",
        f"/api/health-data?owner_id={linked_id}",
        f"/api/health-trends/{domain_id}?owner_id={linked_id}",
        f"/api/health/trends/{indicator_id}?owner_id={linked_id}",
    ):
        denied = client.get(path, headers=headers)
        assert denied.status_code == 403, (path, denied.get_json())
        assert denied.get_json()["code"] == "CURRENT_ACCOUNT_REQUIRED"

    assert client.get(
        f"/api/exam-reports/{linked_report_id}",
        headers=headers,
    ).status_code == 404

    analyzable = client.get("/api/ai/records", headers=headers)
    assert analyzable.status_code == 200, analyzable.get_json()
    assert {
        row["owner_id"] for row in analyzable.get_json()["items"]
    }.issubset({current_id})

    selected = client.post(
        "/api/ai/analyze/stream",
        headers=headers,
        json={"selected_record_ids": [linked_report_id]},
    )
    assert selected.status_code == 404
    assert selected.get_json()["error"]["code"] == "record_unavailable"

    trend = client.post(
        "/api/ai/trends/stream",
        headers=headers,
        json={"domain_id": domain_id, "owner_id": linked_id},
    )
    assert trend.status_code == 403
    assert trend.get_json()["error"]["code"] == "CURRENT_ACCOUNT_REQUIRED"


def test_admin_identity_correction_queues_email_only(app, client):
    admin_headers, _ = _login(client, "admin", "admin123")
    with app.app_context():
        target = User.query.filter_by(username="test6").one()
        target_id = target.id
        before_notifications = UserNotification.query.filter_by(
            user_id=target_id,
            event_type="user_identity_corrected",
        ).count()

    corrected = client.put(
        f"/api/admin/users/{target_id}/basic-profile",
        headers=admin_headers,
        json={
            "real_name": "虚构测试用户六",
            "gender": "undisclosed",
            "birth_date": "1996-06-06",
        },
    )
    assert corrected.status_code == 200, corrected.get_json()
    assert corrected.get_json()["item"]["identity_completed"] is True
    assert corrected.get_json()["item"]["identity_completed_at"]
    assert corrected.get_json()["delivery"]["status"] == "pending"

    with app.app_context():
        assert UserNotification.query.filter_by(
            user_id=target_id,
            event_type="user_identity_corrected",
        ).count() == before_notifications
        outboxes = NotificationOutbox.query.filter_by(
            event_type="user_identity_corrected",
        ).all()
        assert len(outboxes) == 1
        assert outboxes[0].idempotency_key.startswith(
            f"user:{target_id}:identity-corrected:"
        )
        assert outboxes[0].recipient


def test_health_id_resolution_deduplicates_self_and_active_linked_accounts(
    app,
    client,
):
    test1_headers, _ = _login(client, "test1")
    test2_headers, _ = _login(client, "test2")
    with app.app_context():
        test1 = User.query.filter_by(username="test1").one()
        test2 = User.query.filter_by(username="test2").one()
        relation = FriendRelation.query.filter(
            db.or_(
                db.and_(
                    FriendRelation.user_id == test1.id,
                    FriendRelation.friend_user_id == test2.id,
                ),
                db.and_(
                    FriendRelation.user_id == test2.id,
                    FriendRelation.friend_user_id == test1.id,
                ),
            )
        ).one()
        relation_id = relation.id
        self_health_id = test1.health_id
        linked_health_id = test2.health_id

    if not relation.is_active:
        accepted = client.put(
            f"/api/friends/{relation_id}/authorization",
            headers=(
                test2_headers
                if relation.friend_user_id == test2.id
                else test1_headers
            ),
            json={"health_view_granted_by_me": True},
        )
        assert accepted.status_code == 200, accepted.get_json()

    with app.app_context():
        token_count_before = BookingParticipantToken.query.count()

    own = client.post(
        "/api/booking-participants/resolve",
        headers=test1_headers,
        json={"health_id": self_health_id},
    )
    assert own.status_code == 200, own.get_json()
    own_item = own.get_json()["item"]
    assert own_item["participant_type"] == "self"
    assert "participant_token" not in own_item

    linked = client.post(
        "/api/booking-participants/resolve",
        headers=test1_headers,
        json={"health_id": linked_health_id},
    )
    assert linked.status_code == 200, linked.get_json()
    linked_item = linked.get_json()["item"]
    assert linked_item["participant_type"] == "linked_account"
    assert linked_item["relation_id"] == relation_id
    assert "participant_token" not in linked_item

    with app.app_context():
        assert BookingParticipantToken.query.count() == token_count_before


def test_health_id_token_dto_and_missing_proxy_intake_fill(app, client):
    booker_headers, _ = _login(client, "test1")
    target_headers, target_registration = _register(client, "token_target_v12")
    target = _complete(
        client,
        target_headers,
        "周青",
        gender="female",
        birth_date="1988-03-04",
    )
    with app.app_context():
        target_user = User.query.filter_by(username="token_target_v12").one()
        height = IndicatorDict.query.filter_by(code="HEIGHT").one()
        db.session.add(
            SelfMeasurement(
                user_id=target_user.id,
                indicator_dict_id=height.id,
                value=170,
                measured_at=datetime.now(timezone.utc),
            )
        )
        institution = (
            Institution.query.filter_by(is_active=True)
            .order_by(Institution.id.desc())
            .first()
        )
        package = _package_for(institution)
        institution_id, package_id = institution.id, package.id
        package_version_id = package.current_version_id
        target_id = target_user.id
        db.session.commit()

    resolved = client.post(
        "/api/booking-participants/resolve",
        headers=booker_headers,
        json={"health_id": target["health_id"]},
    )
    assert resolved.status_code == 200, resolved.get_json()
    item = resolved.get_json()["item"]
    assert item["participant_type"] == "health_code_token"
    assert item["real_name"] == "周青"
    assert item["gender"] == "female"
    assert item["birth_year"] == 1988
    assert item["masked_health_id"] != target["health_id"]
    assert item["has_recent_height"] is True
    assert item["has_recent_weight"] is False
    for forbidden in ("user_id", "height_cm", "weight_kg", "phone", "email"):
        assert forbidden not in item

    day = date.today() + timedelta(days=29)
    booked = client.post(
        "/api/booking-groups",
        headers=booker_headers,
        json={
            "institution_id": institution_id,
            "package_id": package_id,
            "appointment_date": day.isoformat(),
            "notice_confirmed": True,
            "participants": [
                {
                    "type": "health_code_token",
                    "participant_token": item["participant_token"],
                    # The organizer explicitly chooses a point-in-time manual
                    # snapshot even though a recent server-side height exists.
                    "height_cm": 168,
                    "weight_kg": 62,
                }
            ],
        },
    )
    assert booked.status_code == 201, booked.get_json()
    participant = booked.get_json()["item"]["participants"][0]
    assert participant["participant_type"] == "health_code_token"
    assert "height_cm" not in participant
    assert "weight_kg" not in participant
    with app.app_context():
        appointment = Appointment.query.filter_by(
            user_id=target_id,
            appointment_date=day,
        ).one()
        assert float(appointment.height_cm_snapshot) == 168
        assert float(appointment.weight_kg_snapshot) == 62

    reused = client.post(
        "/api/booking-groups",
        headers=booker_headers,
        json={
            "institution_id": institution_id,
            "package_id": package_id,
            "appointment_date": (day - timedelta(days=1)).isoformat(),
            "notice_confirmed": True,
            "participants": [
                {
                    "type": "health_code_token",
                    "participant_token": item["participant_token"],
                    "weight_kg": 62,
                }
            ],
        },
    )
    assert reused.status_code == 409
    assert reused.get_json()["code"] == "PARTICIPANT_TOKEN_EXPIRED"

    outstanding = client.post(
        "/api/booking-participants/resolve",
        headers=booker_headers,
        json={"health_id": target["health_id"]},
    )
    assert outstanding.status_code == 200
    with app.app_context():
        target_user = db.session.get(User, target_id)
        booker = User.query.filter_by(username="test1").one()
        outstanding_row = (
            BookingParticipantToken.query.filter_by(
                subject_user_id=target_id,
                consumed_at=None,
                revoked_at=None,
            )
            .order_by(BookingParticipantToken.id.desc())
            .first()
        )
        subscription = WaitlistSubscription(
            subscriber_user_id=booker.id,
            institution_id=institution_id,
            package_id=package_id,
            package_version_id=package_version_id,
            appointment_date=day - timedelta(days=1),
            party_size=1,
            notification_email=booker.email,
            status="active",
        )
        db.session.add(subscription)
        db.session.flush()
        db.session.add(
            WaitlistSubscriptionParticipant(
                subscription_id=subscription.id,
                subject_user_id=target_id,
                name_snapshot=target_user.real_name,
                health_id_snapshot=target_user.health_id,
                participant_type="health_code_token",
                authorization_version=target_user.booking_authorization_version,
                booking_authorized_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        token_row_id = outstanding_row.id
        subscription_id = subscription.id

    disabled = client.put(
        "/api/profile/me",
        headers=target_headers,
        json={"health_id_booking_enabled": False},
    )
    assert disabled.status_code == 200, disabled.get_json()
    assert (
        disabled.get_json()["item"]["allow_health_id_proxy_booking"]
        is False
    )
    assert disabled.get_json()["item"]["health_id_booking_enabled"] is False
    with app.app_context():
        assert db.session.get(
            BookingParticipantToken,
            token_row_id,
        ).revoked_at is not None
        subscription = db.session.get(
            WaitlistSubscription,
            subscription_id,
        )
        assert subscription.status == "invalid"
        assert subscription.closed_at is not None
        formal_appointment = Appointment.query.filter_by(
            user_id=target_id,
            appointment_date=day,
        ).one()
        assert formal_appointment.status == "unfulfilled"
        notified_user_ids = {
            row.user_id
            for row in UserNotification.query.filter_by(
                event_type="health_code_booking_disabled",
            ).all()
        }
        assert target_id in notified_user_ids
        assert len(notified_user_ids) == 2


def test_demo_proxy_booking_intake_boundaries(app, client):
    headers, _ = _login(client, "test1")
    with app.app_context():
        users = {
            name: User.query.filter_by(username=name).one()
            for name in ("test2", "test3", "test4", "test5", "test6")
        }
        measurement_codes = {}
        for name, user in users.items():
            measurement_codes[name] = {
                row.indicator_dict.code
                for row in SelfMeasurement.query.filter_by(user_id=user.id).all()
            }
        test5_health_id = users["test5"].health_id
        assert users["test5"].profile_completed is True
        assert users["test5"].allow_health_id_proxy_booking is False
        assert users["test6"].profile_completed is False

    assert {"HEIGHT", "WEIGHT"}.issubset(measurement_codes["test2"])
    assert "HEIGHT" not in measurement_codes["test3"]
    assert "WEIGHT" in measurement_codes["test3"]
    assert {"HEIGHT", "WEIGHT"}.isdisjoint(measurement_codes["test4"])
    assert "HEIGHT" in measurement_codes["test5"]
    assert "WEIGHT" not in measurement_codes["test5"]
    assert {"HEIGHT", "WEIGHT"}.isdisjoint(measurement_codes["test6"])

    unavailable = client.post(
        "/api/booking-participants/resolve",
        headers=headers,
        json={"health_id": test5_health_id},
    )
    assert unavailable.status_code == 404
    assert unavailable.get_json()["code"] == "HEALTH_ID_PARTICIPANT_UNAVAILABLE"


def test_duplicate_participant_and_cancel_guards_use_stable_codes(client):
    booker_headers, booker_payload = _login(client, "test1")
    target_headers, _ = _register(client, "duplicate_target_v12")
    target = _complete(client, target_headers, "赵宁", gender="male")
    token = client.post(
        "/api/booking-participants/resolve",
        headers=booker_headers,
        json={"health_id": target["health_id"]},
    ).get_json()["item"]["participant_token"]
    duplicated = client.post(
        "/api/booking-groups",
        headers=booker_headers,
        json={
            "institution_id": 1,
            "package_id": 1,
            "appointment_date": (date.today() + timedelta(days=28)).isoformat(),
            "notice_confirmed": True,
            "participants": [
                {"type": "health_code_token", "participant_token": token},
                {"type": "health_code_token", "participant_token": token},
            ],
        },
    )
    assert duplicated.status_code == 400
    assert duplicated.get_json()["code"] == "PARTICIPANT_DUPLICATED"

    incomplete_headers, _ = _register(client, "cancel_guard_v12")
    for method, path in (
        ("post", "/api/appointments/999999/cancel"),
        ("post", "/api/booking-groups/999999/cancel"),
        ("delete", "/api/waitlist-subscriptions/999999"),
    ):
        response = getattr(client, method)(path, headers=incomplete_headers)
        assert response.status_code == 409
        assert response.get_json()["code"] == "IDENTITY_REQUIRED"

    invalid_date = client.get(
        "/api/appointments/availability",
        headers=booker_headers,
        query_string={"appointment_date": date.today().isoformat()},
    )
    assert invalid_date.status_code == 400
    assert invalid_date.get_json()["code"] == "BOOKING_DATE_INVALID"
