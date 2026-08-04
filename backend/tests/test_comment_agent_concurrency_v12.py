import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier

from flask_jwt_extended import create_access_token

from app import create_app
from app.agent.crypto import encrypt_json
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    AgentActionExecution,
    AgentPendingAction,
    AgentRun,
    AgentThread,
    Comment,
    CommentAppeal,
    CommentReply,
    CommentSanction,
    Institution,
    SupportHandoff,
    User,
    UserNotification,
)


def _access_token(user):
    return create_access_token(
        identity=str(user.id),
        additional_claims={
            "role": user.role,
            "token_version": user.token_version,
        },
    )


def _file_database_app(tmp_path, monkeypatch, filename):
    database_path = tmp_path / filename
    monkeypatch.setattr(
        TestingConfig,
        "SQLALCHEMY_DATABASE_URI",
        f"sqlite:///{database_path.as_posix()}",
    )
    return create_app("testing")


def _new_completed_user(suffix):
    user = User(
        username=f"moderation_race_{suffix}",
        email=f"moderation-race-{suffix}@example.test",
        role="user",
        health_id=f"HID-RACE{suffix.upper():0<4}"[:12],
        real_name=f"虚构并发用户{suffix}",
        gender="undisclosed",
        birth_date=date(1990, 1, 1),
        identity_completed_at=datetime.now(timezone.utc),
        is_active=True,
    )
    user.set_password("moderation-race-password")
    return user


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


def test_comment_review_sanction_and_appeal_races_have_one_winner(
    tmp_path,
    monkeypatch,
):
    concurrent_app = _file_database_app(
        tmp_path,
        monkeypatch,
        "comment-moderation-cas.db",
    )
    with concurrent_app.app_context():
        admins = User.query.filter_by(role="admin").order_by(User.id).limit(2).all()
        assert len(admins) == 2
        institution = Institution.query.order_by(Institution.id).first()
        reply_user = _new_completed_user("reply")
        sanction_user = _new_completed_user("sanction")
        appeal_user = _new_completed_user("appeal")
        db.session.add_all([reply_user, sanction_user, appeal_user])
        db.session.flush()

        comment = Comment(
            user_id=reply_user.id,
            institution_id=institution.id,
            content="虚构并发审核评价",
            rating=3,
            is_visible=True,
        )
        db.session.add(comment)
        db.session.flush()
        reply = CommentReply(
            comment_id=comment.id,
            institution_id=institution.id,
            content="虚构并发审核机构回复",
            status="pending",
        )
        appeal_sanction = CommentSanction(
            user_id=appeal_user.id,
            reason="虚构并发申诉禁言",
            duration_days=7,
            status="active",
            starts_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            created_by_admin_id=admins[0].id,
        )
        db.session.add_all([reply, appeal_sanction])
        db.session.flush()
        appeal = CommentAppeal(
            sanction_id=appeal_sanction.id,
            user_id=appeal_user.id,
            content="虚构并发申诉内容",
            status="pending",
        )
        db.session.add(appeal)
        db.session.commit()
        reply_id = reply.id
        sanction_user_id = sanction_user.id
        appeal_id = appeal.id
        appeal_user_id = appeal_user.id
        appeal_sanction_id = appeal_sanction.id
        admin_tokens = [_access_token(admin) for admin in admins]

    from app.comments import routes as comment_routes

    original_reply_cas = comment_routes._review_reply_cas
    reply_barrier = Barrier(2)

    def synchronized_reply_cas(*args, **kwargs):
        reply_barrier.wait(timeout=10)
        return original_reply_cas(*args, **kwargs)

    monkeypatch.setattr(comment_routes, "_review_reply_cas", synchronized_reply_cas)

    def review_reply(decision, token):
        path = f"/api/comments/replies/{reply_id}/{decision}"
        payload = {"review_note": "虚构并发驳回说明"} if decision == "reject" else None
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                path,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reply_results = [
            future.result(timeout=20)
            for future in (
                executor.submit(review_reply, "approve", admin_tokens[0]),
                executor.submit(review_reply, "reject", admin_tokens[1]),
            )
        ]
    assert sorted(status for status, _payload in reply_results) == [200, 409]
    with concurrent_app.app_context():
        stored_reply = db.session.get(CommentReply, reply_id)
        assert stored_reply.status in {"approved", "rejected"}
        assert stored_reply.reviewed_by_user_id in {admin.id for admin in admins}

    original_sanction_slot = comment_routes._claim_comment_sanction_slot
    sanction_barrier = Barrier(2)

    def synchronized_sanction_slot(user_id):
        sanction_barrier.wait(timeout=10)
        return original_sanction_slot(user_id)

    monkeypatch.setattr(
        comment_routes,
        "_claim_comment_sanction_slot",
        synchronized_sanction_slot,
    )

    def create_sanction(token, duration_days):
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                "/api/comments/moderation/sanctions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "user_id": sanction_user_id,
                    "reason": "虚构并发禁言原因",
                    "duration_days": duration_days,
                },
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        sanction_results = [
            future.result(timeout=20)
            for future in (
                executor.submit(create_sanction, admin_tokens[0], 7),
                executor.submit(create_sanction, admin_tokens[1], 30),
            )
        ]
    assert sorted(status for status, _payload in sanction_results) == [201, 409]
    with concurrent_app.app_context():
        active_sanctions = CommentSanction.query.filter_by(
            user_id=sanction_user_id,
            status="active",
        ).all()
        assert len(active_sanctions) == 1
        assert UserNotification.query.filter_by(
            user_id=sanction_user_id,
            event_type="comment_sanction_created",
        ).count() == 1

    original_appeal_cas = comment_routes._review_appeal_cas
    appeal_barrier = Barrier(2)

    def synchronized_appeal_cas(*args, **kwargs):
        appeal_barrier.wait(timeout=10)
        return original_appeal_cas(*args, **kwargs)

    monkeypatch.setattr(comment_routes, "_review_appeal_cas", synchronized_appeal_cas)

    def review_appeal(decision, token):
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/comments/appeals/{appeal_id}/{decision}",
                headers={"Authorization": f"Bearer {token}"},
                json={"review_note": f"虚构并发申诉{decision}说明"},
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        appeal_results = [
            future.result(timeout=20)
            for future in (
                executor.submit(review_appeal, "approve", admin_tokens[0]),
                executor.submit(review_appeal, "reject", admin_tokens[1]),
            )
        ]
    assert sorted(status for status, _payload in appeal_results) == [200, 409]
    with concurrent_app.app_context():
        stored_appeal = db.session.get(CommentAppeal, appeal_id)
        stored_sanction = db.session.get(CommentSanction, appeal_sanction_id)
        assert stored_appeal.status in {"approved", "rejected"}
        expected_sanction_status = (
            "lifted" if stored_appeal.status == "approved" else "active"
        )
        assert stored_sanction.status == expected_sanction_status
        event_type = (
            "comment_sanction_lifted"
            if stored_appeal.status == "approved"
            else "comment_appeal_rejected"
        )
        assert UserNotification.query.filter_by(
            user_id=appeal_user_id,
            event_type=event_type,
        ).count() == 1


def test_agent_approve_and_reject_race_executes_only_the_winning_decision(
    tmp_path,
    monkeypatch,
):
    concurrent_app = _file_database_app(
        tmp_path,
        monkeypatch,
        "agent-decision-cas.db",
    )
    with concurrent_app.app_context():
        user = User.query.filter_by(username="test1").one()
        thread_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        action_id = str(uuid.uuid4())
        thread = AgentThread(
            id=thread_id,
            user_id=user.id,
            encrypted_state=encrypt_json([], purpose=f"agent-thread:{thread_id}"),
        )
        db.session.add(thread)
        db.session.flush()
        run = AgentRun(
            id=run_id,
            thread_id=thread_id,
            user_id=user.id,
            status="waiting_approval",
            model_name="concurrency-test",
        )
        db.session.add(run)
        db.session.flush()
        action = AgentPendingAction(
            id=action_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user.id,
            action_type="support_handoff",
            encrypted_payload=encrypt_json(
                {
                    "category": "account",
                    "priority": "normal",
                    "summary": "虚构 Agent 并发转人工请求",
                },
                purpose=f"agent-action:{action_id}",
            ),
            summary={"操作": "虚构并发转人工"},
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        db.session.add(action)
        db.session.commit()
        token = _access_token(user)

    from app.agent import routes as agent_routes

    original_claim = agent_routes._claim_action_decision
    decision_barrier = Barrier(2)

    def synchronized_claim(*args, **kwargs):
        decision_barrier.wait(timeout=10)
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(agent_routes, "_claim_action_decision", synchronized_claim)

    def decide(decision):
        with concurrent_app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/agent/actions/{action_id}/decision/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={"decision": decision},
            )
            return _sse_events(response)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=30)
            for future in (
                executor.submit(decide, "approve"),
                executor.submit(decide, "reject"),
            )
        ]

    terminal_events = [
        (event, payload)
        for events in results
        for event, payload in events
        if event in {"done", "error"}
    ]
    assert len(terminal_events) == 2
    assert sum(event == "done" for event, _payload in terminal_events) == 1
    assert sum(event == "error" for event, _payload in terminal_events) == 1
    assert next(
        payload for event, payload in terminal_events if event == "error"
    )["code"] == "action_conflict"

    with concurrent_app.app_context():
        stored = db.session.get(AgentPendingAction, action_id)
        if stored.status == "executed":
            assert SupportHandoff.query.filter_by(id=action_id).count() == 1
            assert AgentActionExecution.query.filter_by(action_id=action_id).count() == 1
        else:
            assert stored.status == "rejected"
            assert SupportHandoff.query.filter_by(id=action_id).count() == 0
            assert AgentActionExecution.query.filter_by(action_id=action_id).count() == 0
