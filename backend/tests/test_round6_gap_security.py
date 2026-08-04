import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.ai import routes as ai_routes
from app.agent.crypto import decrypt_json, encrypt_json
from app.agent import runtime as agent_runtime
from app.agent.routes import _redact_participant_tokens
from app.agent.runtime import _resolve_participant_slots
from app.agent.schemas import AvailabilityArgs, ComparePackagesArgs
from app.agent.tools import _read_availability, _read_packages
from app.ai.service import AiToolCompletion
from app.extensions import db
from app.models import (
    AgentThread,
    Appointment,
    BookingParticipantAuthorization,
    BookingParticipantToken,
    Comment,
    FriendRelation,
    Institution,
    InstitutionAudienceInsightCache,
    NotificationOutbox,
    Package,
    User,
)
from app.services.audience_insights import get_audience_insight
from app.services.booking_participants import (
    PARTICIPANT_TOKEN_BODY_LENGTH,
    PARTICIPANT_TOKEN_PREFIX,
)
from sqlalchemy.exc import IntegrityError


def _login(client, username, password="Shuhealthdoc！"):
    response = client.post(
        "/api/auth/login",
        json=client.login_payload(username, password),
    )
    assert response.status_code == 200
    return {
        "Authorization": f"Bearer {response.get_json()['access_token']}"
    }


def _register_completed(client, username):
    registered = client.post(
        "/api/auth/register",
        json=client.register_payload(
            username,
            email=f"{username}@example.test",
        ),
    )
    assert registered.status_code == 201
    headers = {
        "Authorization": f"Bearer {registered.get_json()['access_token']}"
    }
    completed = client.post(
        "/api/profile/me/complete",
        headers=headers,
        json={
            "real_name": f"虚构{username}",
            "gender": "undisclosed",
            "birth_date": "1990-01-01",
        },
    )
    assert completed.status_code == 200
    return headers


def test_agent_preprocesses_health_id_before_model_and_storage(
    client,
    app,
    monkeypatch,
):
    headers = _login(client, "test1")
    _register_completed(client, "agent_health_code_target_v12")
    created = client.post("/api/agent/threads", headers=headers)
    assert created.status_code == 201
    thread_id = created.get_json()["item"]["id"]
    with app.app_context():
        raw_health_id = User.query.filter_by(
            username="agent_health_code_target_v12"
        ).one().health_id
        token_count_before = BookingParticipantToken.query.count()

    captured = {}

    def fake_run_agent(**state):
        captured["message"] = state["message"]
        captured["messages"] = state["messages"]
        slot_id = state["message"].split(
            "participant_token=",
            1,
        )[1].split("；", 1)[0]
        captured["slot_id"] = slot_id
        captured["participant_slots"] = state["participant_slots"]
        participant_token = state["participant_slots"][slot_id][
            "participant_token"
        ]
        return {
            "answer": f"已安全识别{participant_token}请确认",
            "events": [{
                "event": "evidence",
                "data": {
                    f"中{participant_token}文": [
                        f"X{participant_token}Y",
                    ]
                },
            }],
            "usage": {},
            "intent": "booking",
        }

    monkeypatch.setattr("app.agent.routes.run_agent", fake_run_agent)
    response = client.post(
        f"/api/agent/threads/{thread_id}/runs/stream",
        headers=headers,
        json={"message": f"请用健康身份码 X{raw_health_id}Y 帮我预约"},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert raw_health_id not in captured["message"]
    assert "participant_token=participant_slot_" in captured["message"]
    assert "bpt_" not in captured["message"]
    assert "bpt_" not in json.dumps(captured["messages"], ensure_ascii=False)
    assert "身份码=HI" in captured["message"]
    assert "bpt_" not in body
    assert "participant_slot_" not in body
    assert "[安全参与人凭证]" in body

    with app.app_context():
        assert BookingParticipantToken.query.count() == token_count_before + 1
        token = BookingParticipantToken.query.order_by(
            BookingParticipantToken.id.desc()
        ).first()
        assert raw_health_id not in token.token_hash
        raw_token = captured["participant_slots"][captured["slot_id"]][
            "participant_token"
        ]
        assert len(raw_token) == len(PARTICIPANT_TOKEN_PREFIX) + (
            PARTICIPANT_TOKEN_BODY_LENGTH
        )
        assert re.fullmatch(r"bpt_[A-Za-z0-9_-]{43}", raw_token)
        thread = db.session.get(AgentThread, thread_id)
        state = decrypt_json(
            thread.encrypted_state,
            purpose=f"agent-thread:{thread_id}",
        )
        serialized = str(state["messages"])
        assert raw_health_id not in serialized
        assert "participant_token=participant_slot_" in serialized
        assert "bpt_" not in serialized
        assert (
            state["participant_slots"][captured["slot_id"]][
                "participant_token"
            ]
            == raw_token
        )
    rendered_thread = client.get(
        f"/api/agent/threads/{thread_id}",
        headers=headers,
    )
    assert rendered_thread.status_code == 200
    assert "bpt_" not in rendered_thread.get_data(as_text=True)
    assert "participant_slot_" not in rendered_thread.get_data(as_text=True)


def test_participant_token_redaction_covers_unicode_ascii_and_nested_values():
    token = "bpt_" + "A" * 43
    second = "bpt_" + "z" * 43
    value = {
        f"中{token}文": [
            token,
            f"X{token}Y",
            f"_{token}_",
            f"🙂{token}！",
            f"{token}{second}",
            {"arguments": f"prefix-{token}-suffix"},
        ]
    }

    redacted = _redact_participant_tokens(value)
    serialized = json.dumps(redacted, ensure_ascii=False)
    assert "bpt_" not in serialized
    assert token not in serialized
    assert second not in serialized
    assert serialized.count("[安全参与人凭证]") == 8


def test_agent_scrubs_legacy_health_ids_before_model_output_and_storage(
    app,
    client,
    monkeypatch,
):
    headers = _login(client, "test1")
    thread_id = client.post(
        "/api/agent/threads",
        headers=headers,
    ).get_json()["item"]["id"]
    with app.app_context():
        raw_health_id = User.query.filter_by(username="test2").one().health_id
        thread = db.session.get(AgentThread, thread_id)
        thread.encrypted_state = encrypt_json(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": f"旧消息 X{raw_health_id}Y",
                    },
                    {
                        "role": "assistant",
                        "content": f"旧回复 {raw_health_id}",
                    },
                ],
                "active_subject_id": thread.user_id,
            },
            purpose=f"agent-thread:{thread_id}",
        )
        db.session.commit()

    before = client.get(f"/api/agent/threads/{thread_id}", headers=headers)
    assert before.status_code == 200
    assert raw_health_id not in before.get_data(as_text=True)
    captured = {}

    def fake_run_agent(**state):
        captured["message"] = state["message"]
        captured["messages"] = state["messages"]
        return {
            "answer": f"供应商输出 X{raw_health_id}Y",
            "messages": [
                *state["messages"],
                {"role": "user", "content": state["message"]},
                {
                    "role": "assistant",
                    "content": f"模型历史 {raw_health_id}",
                    "metadata": {"raw": f"X{raw_health_id}Y"},
                },
            ],
            "events": [{
                "event": "evidence",
                "data": {"nested": [f"事件 {raw_health_id}"]},
            }],
            "usage": {},
            "intent": "general",
        }

    monkeypatch.setattr("app.agent.routes.run_agent", fake_run_agent)
    response = client.post(
        f"/api/agent/threads/{thread_id}/runs/stream",
        headers=headers,
        json={"message": "继续刚才的话题"},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert raw_health_id not in captured["message"]
    assert raw_health_id not in json.dumps(
        captured["messages"], ensure_ascii=False
    )
    assert raw_health_id not in body

    with app.app_context():
        thread = db.session.get(AgentThread, thread_id)
        state = decrypt_json(
            thread.encrypted_state,
            purpose=f"agent-thread:{thread_id}",
        )
        assert raw_health_id not in json.dumps(
            state["messages"], ensure_ascii=False
        )
    after = client.get(f"/api/agent/threads/{thread_id}", headers=headers)
    assert raw_health_id not in after.get_data(as_text=True)


def test_agent_runtime_resolves_slot_only_at_typed_tool_boundary(
    app,
    monkeypatch,
):
    slot_id = "participant_slot_" + "1" * 32
    raw_token = "bpt_" + "B" * 43
    captured = {}

    class SlotClient:
        model = "slot-regression"

        def complete_with_tools(self, messages, _tools, **_kwargs):
            captured["model_messages"] = messages
            arguments = {
                "institution_id": 1,
                "package_id": 1,
                "appointment_date": "2026-08-05",
                "participants": [{
                    "type": "health_code_token",
                    "participant_token": slot_id,
                }],
                "notice_confirmed": True,
            }
            tool_call = {
                "id": "slot-call",
                "type": "function",
                "function": {
                    "name": "create_booking_draft",
                    "arguments": json.dumps(arguments),
                },
            }
            return AiToolCompletion(
                content="",
                tool_calls=[tool_call],
                usage={},
                message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            )

    def fake_execute(_name, arguments, **_kwargs):
        captured["tool_arguments"] = arguments
        return {
            "approval_required": True,
            "summary": {"title": "测试预约草稿"},
        }

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda _config: SlotClient())
    monkeypatch.setattr(agent_runtime, "execute_tool", fake_execute)
    with app.app_context():
        user = User.query.filter_by(username="test1").one()
        result = agent_runtime._agent_node({
            "message": f"使用 {slot_id} 预约",
            "messages": [],
            "participant_slots": {
                slot_id: {
                    "participant_token": raw_token,
                    "expires_at": "2099-01-01T00:00:00+00:00",
                }
            },
            "user": user,
            "thread_id": "slot-thread",
            "run_id": "slot-run",
        })

    assert raw_token not in json.dumps(
        captured["model_messages"], ensure_ascii=False
    )
    assert captured["tool_arguments"]["participants"][0][
        "participant_token"
    ] == raw_token
    assert "待确认" in result["answer"]
    # A slot-looking value in an unrelated field is intentionally untouched.
    assert _resolve_participant_slots(
        {"note": slot_id},
        {slot_id: {"participant_token": raw_token}},
    )["note"] == slot_id


def test_agent_health_id_uses_canonical_linked_account_without_token(
    client,
    app,
    monkeypatch,
):
    headers = _login(client, "test1")
    created = client.post("/api/agent/threads", headers=headers)
    assert created.status_code == 201
    thread_id = created.get_json()["item"]["id"]
    with app.app_context():
        test1 = User.query.filter_by(username="test1").one()
        test2 = User.query.filter_by(username="test2").one()
        relation = FriendRelation.query.filter_by(
            user_id=test1.id,
            friend_user_id=test2.id,
        ).one()
        if not relation.is_active:
            relation.activate()
            db.session.commit()
        raw_health_id = test2.health_id
        relation_id = relation.id
        token_count_before = BookingParticipantToken.query.count()

    captured = {}

    def fake_run_agent(**state):
        captured["message"] = state["message"]
        return {
            "answer": "已按关联账号加入预约。",
            "events": [],
            "usage": {},
            "intent": "booking",
        }

    monkeypatch.setattr("app.agent.routes.run_agent", fake_run_agent)
    response = client.post(
        f"/api/agent/threads/{thread_id}/runs/stream",
        headers=headers,
        json={"message": f"请用健康身份码 {raw_health_id} 帮我预约"},
    )
    assert response.status_code == 200
    response.get_data(as_text=True)
    assert raw_health_id not in captured["message"]
    assert "participant_type=linked_account" in captured["message"]
    assert f"relation_id={relation_id}" in captured["message"]
    assert "participant_token=" not in captured["message"]
    with app.app_context():
        assert BookingParticipantToken.query.count() == token_count_before


def test_agent_health_id_failure_is_indistinguishable_and_not_persisted(
    client,
    app,
):
    headers = _login(client, "test1")
    thread_id = client.post(
        "/api/agent/threads",
        headers=headers,
    ).get_json()["item"]["id"]
    with app.app_context():
        token_count_before = BookingParticipantToken.query.count()
    response = client.post(
        f"/api/agent/threads/{thread_id}/runs/stream",
        headers=headers,
        json={"message": "请帮 HID-AAAAAAAA 预约体检"},
    )
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == (
        "HEALTH_ID_PARTICIPANT_UNAVAILABLE"
    )
    with app.app_context():
        assert BookingParticipantToken.query.count() == token_count_before
        thread = db.session.get(AgentThread, thread_id)
        state = decrypt_json(
            thread.encrypted_state,
            purpose=f"agent-thread:{thread_id}",
        )
        assert state["messages"] == []


def test_organization_deactivate_restore_never_revives_institution_jwt(
    app,
    client,
):
    login_response = client.post(
        "/api/auth/login",
        json=client.login_payload(
            "institution1_staff1",
            "Shuhealthdoc！",
        ),
    )
    assert login_response.status_code == 200, login_response.get_json()
    login = login_response.get_json()
    institution_headers = {
        "Authorization": f"Bearer {login['access_token']}"
    }
    admin_headers = _login(client, "admin", "admin123")
    with app.app_context():
        staff = User.query.filter_by(username="institution1_staff1").one()
        organization = staff.managed_institution.organization
        organization_id = organization.id
        initial_versions = {
            administrator.id: administrator.token_version
            for branch in organization.branches
            for administrator in branch.administrators
        }

    assert client.get(
        "/api/org/dashboard",
        headers=institution_headers,
    ).status_code == 200
    disabled = client.post(
        f"/api/admin/organizations/{organization_id}/deactivate",
        headers=admin_headers,
    )
    assert disabled.status_code == 200, disabled.get_json()
    assert client.get(
        "/api/org/dashboard",
        headers=institution_headers,
    ).status_code == 401
    restored = client.post(
        f"/api/admin/organizations/{organization_id}/restore",
        headers=admin_headers,
    )
    assert restored.status_code == 200, restored.get_json()

    assert client.get(
        "/api/org/dashboard",
        headers=institution_headers,
    ).status_code == 401
    assert client.post(
        "/api/auth/refresh",
        headers={"Authorization": f"Bearer {login['refresh_token']}"},
    ).status_code == 401
    fresh_headers = _login(client, "institution1_staff1")
    assert client.get("/api/org/dashboard", headers=fresh_headers).status_code == 200

    with app.app_context():
        staff = User.query.filter_by(username="institution1_staff1").one()
        organization = staff.managed_institution.organization
        current_versions = {
            administrator.id: administrator.token_version
            for branch in organization.branches
            for administrator in branch.administrators
        }
        assert current_versions == {
            user_id: version + 2
            for user_id, version in initial_versions.items()
        }


def test_disabled_organization_is_excluded_from_every_booking_read_and_write(
    client,
    app,
):
    headers = _login(client, "test1")
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=2)
    with app.app_context():
        institution = Institution.query.filter_by(is_active=True).first()
        package = Package.query.filter(
            Package.institution_id == institution.id,
            Package.is_active.is_(True),
            Package.current_version_id.is_not(None),
        ).first()
        institution_id = institution.id
        branch_name = institution.branch_name
        package_id = package.id
        institution.organization.is_active = False
        db.session.commit()

        assert _read_packages(
            None,
            ComparePackagesArgs(package_ids=[package_id]),
        )["packages"] == []
        try:
            _read_availability(
                None,
                AvailabilityArgs(
                    institution_id=institution_id,
                    appointment_date=day,
                    party_size=1,
                ),
            )
        except LookupError:
            pass
        else:
            raise AssertionError("disabled organization remained Agent-bookable")

    availability = client.get(
        "/api/appointments/availability",
        headers=headers,
        query_string={
            "appointment_date": day.isoformat(),
            "q": branch_name,
        },
    )
    assert availability.status_code == 200
    assert all(
        row["institution"]["id"] != institution_id
        for row in availability.get_json()["items"]
    )

    booking = client.post(
        "/api/booking-groups",
        headers=headers,
        json={
            "institution_id": institution_id,
            "package_id": package_id,
            "appointment_date": day.isoformat(),
            "participants": [{"type": "self"}],
            "notice_confirmed": True,
        },
    )
    assert booking.status_code == 404


def test_authenticated_catalog_does_not_expose_internal_branch_fields(
    client,
    app,
):
    headers = _login(client, "test1")
    response = client.get("/api/institutions", headers=headers)
    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert "notification_email" not in item
    assert "daily_appointment_limit" not in item
    assert "account_deactivated_at" not in item

    organization = client.get("/api/organizations", headers=headers)
    assert organization.status_code == 200
    branch = organization.get_json()["items"][0]["branches"][0]
    assert "notification_email" not in branch
    assert "daily_appointment_limit" not in branch
    assert "account_deactivated_at" not in branch

    day = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)
    availability = client.get(
        "/api/appointments/availability",
        headers=headers,
        query_string={"appointment_date": day.isoformat()},
    )
    assert availability.status_code == 200
    for availability_item in availability.get_json()["items"]:
        for package in availability_item["packages"]:
            assert "is_active" not in package
            assert "current_version_id" not in package


def test_resetting_institution_password_retires_old_credential_mail(
    client,
    app,
):
    headers = _login(client, "admin", "admin123")
    with app.app_context():
        institution = Institution.query.join(
            User,
            User.managed_institution_id == Institution.id,
        ).filter(User.role == "institution_admin").first()
        administrator = institution.administrator
        old = NotificationOutbox(
            event_type="institution_account_created",
            idempotency_key=(
                f"institution-account:{administrator.id}:"
                "version:legacy:gap-test"
            ),
            recipient=institution.notification_email,
            payload={
                "encrypted_credentials": "must-not-be-sent",
                "credential_purpose": "gap-test",
            },
            status="failed",
            next_attempt_at=datetime.now(timezone.utc),
        )
        db.session.add(old)
        db.session.commit()
        institution_id = institution.id
        old_id = old.id
        email = institution.notification_email

    response = client.post(
        f"/api/admin/institutions/{institution_id}/account/reset",
        headers=headers,
        json={
            "password": "NewInitialPassword123!",
            "email": email,
        },
    )
    assert response.status_code == 200

    with app.app_context():
        old = db.session.get(NotificationOutbox, old_id)
        administrator = User.query.filter_by(
            managed_institution_id=institution_id,
        ).one()
        newest = NotificationOutbox.query.filter_by(
            event_type="institution_account_reset",
        ).filter(
            NotificationOutbox.idempotency_key.like(
                f"institution-account:{administrator.id}:%"
            )
        ).order_by(NotificationOutbox.id.desc()).first()
        assert old.status == "failed"
        assert old.payload == {
            "sensitive_content_cleared": True,
            "superseded_by_password_reset": True,
        }
        assert old.sensitive_payload_cleared_at is not None
        assert old.next_attempt_at.year == 9999
        assert newest is not None
        assert newest.status == "pending"
        assert newest.payload.get("encrypted_credentials")
        assert administrator.check_password("NewInitialPassword123!")


def test_inactive_friend_relation_uses_canonical_v12_error_code(
    client,
    app,
):
    requester = _register_completed(client, "gap_relation_requester")
    target = _register_completed(client, "gap_relation_target")
    target_profile = client.get("/api/profile/me", headers=target)
    assert target_profile.status_code == 200
    relation_response = client.post(
        "/api/friends",
        headers=requester,
        json={
            "health_id": target_profile.get_json()["item"]["health_id"],
            "relation_name": "虚构待确认亲友",
        },
    )
    assert relation_response.status_code == 201
    relation_id = relation_response.get_json()["item"]["id"]

    switched = client.post(
        f"/api/friends/{relation_id}/switch-session",
        headers=requester,
    )
    assert switched.status_code == 409
    assert switched.get_json()["code"] == "RELATIONSHIP_INACTIVE"

    with app.app_context():
        institution = Institution.query.filter_by(is_active=True).first()
        package = Package.query.filter(
            Package.institution_id == institution.id,
            Package.is_active.is_(True),
            Package.current_version_id.is_not(None),
        ).first()
        institution_id = institution.id
        package_id = package.id
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)
    booking = client.post(
        "/api/booking-groups",
        headers=requester,
        json={
            "institution_id": institution_id,
            "package_id": package_id,
            "appointment_date": day.isoformat(),
            "notice_confirmed": True,
            "participants": [{
                "type": "linked_account",
                "relation_id": relation_id,
            }],
        },
    )
    assert booking.status_code == 409
    assert booking.get_json()["code"] == "RELATIONSHIP_INACTIVE"


def test_authenticated_comment_catalog_keeps_other_accounts_anonymous(
    client,
    app,
):
    headers = _login(client, "test1")
    with app.app_context():
        author = User.query.filter_by(username="test2").one()
        institution = Institution.query.filter_by(is_active=True).first()
        comment = Comment(
            user_id=author.id,
            institution_id=institution.id,
            content="虚构登录态匿名评论验收",
            rating=4,
            is_visible=True,
        )
        db.session.add(comment)
        db.session.commit()
        comment_id = comment.id
        institution_id = institution.id
        username = author.username

    response = client.get(
        "/api/comments",
        headers=headers,
        query_string={"institution_id": institution_id},
    )
    assert response.status_code == 200
    item = next(
        row for row in response.get_json()["items"]
        if row["id"] == comment_id
    )
    assert item["author_display_name"].endswith("***")
    assert "user" not in item
    assert "user_id" not in item
    assert username not in response.get_data(as_text=True)


def test_user_report_view_hides_institution_account_usernames(
    client,
    app,
):
    headers = _login(client, "test1")
    response = client.get("/api/exam-reports", headers=headers)
    assert response.status_code == 200
    items = response.get_json()["items"]
    assert items
    assert all("created_by_username_snapshot" not in item for item in items)
    assert all("reviewed_by_username_snapshot" not in item for item in items)
    assert all("upload_doctor_name" in item for item in items)
    assert all("review_doctor_name" in item for item in items)

    health_data = client.get("/api/health-data", headers=headers)
    assert health_data.status_code == 200
    institution_item = next(
        row for row in health_data.get_json()["items"]
        if row["source_type"] == "institution"
    )
    detail = client.get(
        f"/api/health-data/{institution_item['health_data_id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    trace = detail.get_json()["item"]["review_trace"]
    assert trace["upload_doctor_name"]
    assert trace["review_doctor_name"]
    assert trace["uploaded_at"]
    assert trace["reviewed_at"]
    assert trace["published_at"]
    serialized = detail.get_data(as_text=True)
    assert "created_by_username_snapshot" not in serialized
    assert "reviewed_by_username_snapshot" not in serialized


def test_audience_cache_first_writer_race_reads_committed_winner(
    app,
    monkeypatch,
):
    with app.app_context():
        institution = Institution.query.filter_by(is_active=True).first()
        InstitutionAudienceInsightCache.query.filter_by(
            scope_type="branch",
            scope_id=institution.id,
            period_key="days:30",
        ).delete(synchronize_session=False)
        db.session.commit()
        monkeypatch.setattr(
            "app.services.audience_insights._ai_analysis",
            lambda aggregate, fallback: (
                fallback,
                "deterministic",
                None,
            ),
        )

        session_class = type(db.session())
        real_commit = session_class.commit
        raced = {"value": False}

        def racing_commit(session):
            pending = next(
                (
                    row for row in session.new
                    if isinstance(row, InstitutionAudienceInsightCache)
                    and row.scope_type == "branch"
                    and row.scope_id == institution.id
                    and row.period_key == "days:30"
                ),
                None,
            )
            if pending is None or raced["value"]:
                return real_commit(session)
            raced["value"] = True
            values = {
                "scope_type": pending.scope_type,
                "scope_id": pending.scope_id,
                "period_key": pending.period_key,
                "data_digest": pending.data_digest,
                "aggregate_payload": pending.aggregate_payload,
                "analysis_text": pending.analysis_text,
                "model_name": pending.model_name,
                "source": pending.source,
                "generated_at": pending.generated_at,
                "expires_at": pending.expires_at,
            }
            session.rollback()
            session.add(InstitutionAudienceInsightCache(**values))
            real_commit(session)
            raise IntegrityError(
                "simulated concurrent cache insert",
                {},
                RuntimeError("unique scope race"),
            )

        monkeypatch.setattr(session_class, "commit", racing_commit)
        item, cache_hit = get_audience_insight(
            institution,
            scope="branch",
            period_days=30,
        )
        assert raced["value"] is True
        assert cache_hit is True
        assert item.data_digest
        assert InstitutionAudienceInsightCache.query.filter_by(
            scope_type="branch",
            scope_id=institution.id,
            period_key="days:30",
        ).count() == 1


def test_legacy_single_booking_uses_group_workflow_and_requires_notice(
    client,
    app,
):
    headers = _register_completed(client, "legacy_group_adapter_v12")
    business_today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    with app.app_context():
        package = next(
            row for row in Package.query.filter(
                Package.is_active.is_(True),
                Package.current_version_id.is_not(None),
            ).all()
            if next(
                (
                    version for version in row.versions
                    if version.id == row.current_version_id
                ),
                None,
            ).booking_notice_snapshot
        )
        institution_id = package.institution_id
        package_id = package.id
        user_id = User.query.filter_by(
            username="legacy_group_adapter_v12",
        ).one().id
    payload = {
        "institution_id": institution_id,
        "package_id": package_id,
        "appointment_date": (business_today + timedelta(days=29)).isoformat(),
        "height_cm": 170,
        "weight_kg": 65,
    }
    denied = client.post(
        "/api/appointments",
        headers=headers,
        json=payload,
    )
    assert denied.status_code == 400
    with app.app_context():
        assert Appointment.query.filter_by(user_id=user_id).count() == 0

    created = client.post(
        "/api/appointments",
        headers=headers,
        json={**payload, "notice_confirmed": True},
    )
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["booking_group"]["id"] == (
        created.get_json()["item"]["booking_group_id"]
    )
    with app.app_context():
        appointment = db.session.get(
            Appointment,
            created.get_json()["item"]["id"],
        )
        authorization = BookingParticipantAuthorization.query.filter_by(
            appointment_id=appointment.id,
        ).one()
        assert authorization.participant_type == "self"
        assert authorization.booker_user_id == user_id
        assert authorization.subject_user_id == user_id


def test_report_draft_creates_explicit_uploaded_timeline_event(
    client,
    app,
):
    headers = _register_completed(client, "report_uploaded_event_v12")
    org_headers = _login(client, "institution1_staff1")
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=28)
    with app.app_context():
        staff = User.query.filter_by(username="institution1_staff1").one()
        package = Package.query.filter(
            Package.institution_id == staff.managed_institution_id,
            Package.is_active.is_(True),
            Package.current_version_id.is_not(None),
        ).first()
        institution_id = staff.managed_institution_id
        package_id = package.id
    booking = client.post(
        "/api/appointments",
        headers=headers,
        json={
            "institution_id": institution_id,
            "package_id": package_id,
            "appointment_date": day.isoformat(),
            "height_cm": 170,
            "weight_kg": 65,
            "notice_confirmed": True,
        },
    )
    assert booking.status_code == 201, booking.get_json()
    appointment_id = booking.get_json()["item"]["id"]
    assert client.post(
        f"/api/payment-orders/{booking.get_json()['payment_order']['id']}/pay",
        headers=headers,
    ).status_code == 200
    assert client.post(
        f"/api/org/appointments/{appointment_id}/attend",
        headers=org_headers,
    ).status_code == 200
    report = client.post(
        "/api/org/reports",
        headers=org_headers,
        json={"appointment_id": appointment_id},
    )
    assert report.status_code == 201, report.get_json()
    assert report.get_json()["item"]["status"] == "draft"

    appointments = client.get(
        "/api/appointments",
        headers=headers,
    ).get_json()["items"]
    rendered = next(
        row for row in appointments
        if row["id"] == appointment_id
    )
    uploaded = [
        event for event in rendered["events"]
        if event["type"] == "report_uploaded"
    ]
    assert len(uploaded) == 1
    assert rendered["report_status"] == "draft"


def test_legacy_ai_institution_dates_use_shanghai_business_clock(
    app,
    monkeypatch,
):
    class FixedBusinessDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 23, 30, tzinfo=tz)

    monkeypatch.setattr(ai_routes, "datetime", FixedBusinessDatetime)
    with app.app_context():
        result = ai_routes._institution_context_for_message(
            "请推荐平台体检机构"
        )
    assert "2026-01-02" in result["reply"]
    assert "2026-01-01（" not in result["reply"]
