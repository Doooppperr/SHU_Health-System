import json
import uuid
from datetime import date, timedelta

from app.agent.tools import execute_tool
from app.extensions import db
from app.agent.crypto import decrypt_json
from app.models import (
    AgentActionExecution,
    AgentPendingAction,
    AgentThread,
    AgentToolEvent,
    AgentRun,
    BookingGroup,
    Institution,
    Package,
    SupportHandoff,
    User,
)


def _register(client, username):
    response = client.post(
        "/api/auth/register",
        json=client.register_payload(
            username,
            email=f"{username}@example.test",
        ),
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def _login(client, username, password="Shuhealthdoc！"):
    response = client.post(
        "/api/auth/login", json=client.login_payload(username, password)
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def _sse_events(response):
    events = []
    current = None
    for line in response.get_data(as_text=True).splitlines():
        if line.startswith("event: "):
            current = line.removeprefix("event: ")
        elif line.startswith("data: ") and current:
            events.append((current, json.loads(line.removeprefix("data: "))))
            current = None
    return events


def test_agent_thread_is_encrypted_and_owned(client, app):
    headers = _register(client, "agent-owner")
    response = client.post("/api/agent/threads", headers=headers)
    assert response.status_code == 201
    thread_id = response.get_json()["item"]["id"]

    with app.app_context():
        row = db.session.get(AgentThread, thread_id)
        assert "messages" not in row.encrypted_state
        state = decrypt_json(row.encrypted_state, purpose=f"agent-thread:{thread_id}")
        assert state["messages"] == []

    other_headers = _register(client, "agent-other")
    assert (
        client.get(f"/api/agent/threads/{thread_id}", headers=other_headers).status_code
        == 404
    )


def test_agent_emergency_rule_stops_before_tools(client, app):
    headers = _register(client, "agent-emergency")
    thread_id = client.post("/api/agent/threads", headers=headers).get_json()["item"]["id"]
    response = client.post(
        f"/api/agent/threads/{thread_id}/runs/stream",
        headers=headers,
        json={"message": "我现在突然胸痛而且喘不上气"},
    )
    assert response.status_code == 200
    events = _sse_events(response)
    assert any(name == "status" and data["stage"] == "emergency" for name, data in events)
    assert "120" in "".join(
        data.get("content", "") for name, data in events if name == "delta"
    )
    with app.app_context():
        assert AgentToolEvent.query.count() == 0


def test_agent_mock_uses_typed_read_tool(client, app):
    headers = _register(client, "agent-reader")
    thread_id = client.post("/api/agent/threads", headers=headers).get_json()["item"]["id"]
    response = client.post(
        f"/api/agent/threads/{thread_id}/runs/stream",
        headers=headers,
        json={"message": "列出我的体检报告"},
    )
    events = _sse_events(response)
    assert any(name == "tool_started" and data["name"] == "list_reports" for name, data in events)
    assert any(name == "evidence" and data["tool"] == "list_reports" for name, data in events)
    assert events[-1][0] == "done"
    with app.app_context():
        event = AgentToolEvent.query.one()
        assert event.status == "completed"
        assert event.redacted_input == {"fields": ["limit", "owner_id"]}


def test_support_handoff_requires_confirmation_and_is_idempotent(client, app):
    headers = _register(client, "agent-support")
    thread_id = client.post("/api/agent/threads", headers=headers).get_json()["item"]["id"]
    run_response = client.post(
        f"/api/agent/threads/{thread_id}/runs/stream",
        headers=headers,
        json={"message": "请帮我转人工客服处理账号问题"},
    )
    events = _sse_events(run_response)
    approval = next(data for name, data in events if name == "approval_required")
    action_id = approval["action_id"]
    restored = client.get(f"/api/agent/threads/{thread_id}", headers=headers)
    assert restored.get_json()["item"]["pending_actions"][0]["action_id"] == action_id
    assert "Connection" not in run_response.headers

    with app.app_context():
        action = db.session.get(AgentPendingAction, action_id)
        assert action.status == "pending"
        assert SupportHandoff.query.count() == 0

    first = client.post(
        f"/api/agent/actions/{action_id}/decision/stream",
        headers=headers,
        json={"decision": "approve"},
    )
    assert any(
        name == "done" and data["status"] == "executed"
        for name, data in _sse_events(first)
    )
    second = client.post(
        f"/api/agent/actions/{action_id}/decision/stream",
        headers=headers,
        json={"decision": "approve"},
    )
    assert any(name == "done" for name, _data in _sse_events(second))

    with app.app_context():
        assert SupportHandoff.query.count() == 1
        assert AgentActionExecution.query.count() == 1
        assert db.session.get(AgentPendingAction, action_id).status == "executed"

    admin_headers = _login(client, "admin", "admin123")
    listing = client.get(
        "/api/agent/admin/support-handoffs?status=open",
        headers=admin_headers,
    )
    assert listing.status_code == 200
    assert listing.get_json()["items"][0]["id"] == action_id
    accepted = client.patch(
        f"/api/agent/admin/support-handoffs/{action_id}",
        headers=admin_headers,
        json={"status": "in_progress"},
    )
    assert accepted.status_code == 200
    assert accepted.get_json()["item"]["assigned_to_username"] == "admin"
    resolved = client.patch(
        f"/api/agent/admin/support-handoffs/{action_id}",
        headers=admin_headers,
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200
    assert resolved.get_json()["item"]["resolved_at"]


def test_booking_action_reuses_domain_rules_and_commits_once(client, app):
    headers = _login(client, "test1")
    thread_id = client.post("/api/agent/threads", headers=headers).get_json()["item"]["id"]
    day = date.today() + timedelta(days=29)
    with app.app_context():
        user = User.query.filter_by(username="test1").one()
        institution = Institution.query.filter_by(is_active=True).order_by(Institution.id).first()
        package = (
            Package.query.filter_by(institution_id=institution.id, is_active=True)
            .order_by(Package.id)
            .first()
        )
        run = AgentRun(
            id=str(uuid.uuid4()),
            thread_id=thread_id,
            user_id=user.id,
            model_name="test",
        )
        db.session.add(run)
        db.session.flush()
        draft = execute_tool(
            "create_booking_draft",
            {
                "institution_id": institution.id,
                "package_id": package.id,
                "appointment_date": day.isoformat(),
                "participant_user_ids": [],
                "participant_intakes": [
                    {"user_id": None, "height_cm": 170, "weight_kg": 65}
                ],
                "notice_confirmed": True,
            },
            user=user,
            thread_id=thread_id,
            run_id=run.id,
        )
        db.session.commit()
        action_id = draft["action_id"]

    response = client.post(
        f"/api/agent/actions/{action_id}/decision/stream",
        headers=headers,
        json={"decision": "approve"},
    )
    assert any(
        name == "done" and data["status"] == "executed"
        for name, data in _sse_events(response)
    )
    repeated = client.post(
        f"/api/agent/actions/{action_id}/decision/stream",
        headers=headers,
        json={"decision": "approve"},
    )
    assert any(name == "done" for name, _data in _sse_events(repeated))
    with app.app_context():
        groups = BookingGroup.query.filter_by(
            booked_by_user_id=User.query.filter_by(username="test1").one().id,
            appointment_date=day,
        ).all()
        assert len(groups) == 1
        assert AgentActionExecution.query.filter_by(action_id=action_id).count() == 1
