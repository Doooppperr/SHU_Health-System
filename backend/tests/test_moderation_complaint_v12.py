from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    AppointmentComplaint,
    Comment,
    CommentAppeal,
    CommentReply,
    CommentSanction,
    DelegatedActionAudit,
    FriendRelation,
    Institution,
    InstitutionReport,
    Organization,
    User,
)


PASSWORD = "Shuhealthdoc！"


def login(client, username, password=PASSWORD):
    response = client.post(
        "/api/auth/login",
        json=client.login_payload(username, password),
    )
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def register_completed(client, username):
    response = client.post(
        "/api/auth/register",
        json=client.register_payload(
            username,
            email=f"{username}@example.test",
        ),
    )
    assert response.status_code == 201, response.get_json()
    headers = {
        "Authorization": f"Bearer {response.get_json()['access_token']}"
    }
    completed = client.post(
        "/api/profile/me/complete",
        headers=headers,
        json={
            "real_name": f"虚构{username}",
            "gender": "undisclosed",
            "birth_date": "1991-01-01",
        },
    )
    assert completed.status_code == 200, completed.get_json()
    return headers


def test_comment_moderation_is_scoped_to_effective_account(app, client):
    admin_headers = login(client, "admin", "admin123")
    actor_headers = login(client, "test1")
    institution_headers = login(client, "institution1_staff1")
    with app.app_context():
        admin = User.query.filter_by(username="admin").one()
        actor = User.query.filter_by(username="test1").one()
        linked = User.query.filter_by(username="test2").one()
        actor_report = InstitutionReport.query.filter_by(
            matched_user_id=actor.id,
            status="published",
        ).first()
        linked_report = InstitutionReport.query.filter_by(
            matched_user_id=linked.id,
            status="published",
        ).first()
        assert actor_report is not None and linked_report is not None
        source = Comment(
            user_id=actor.id,
            institution_id=actor_report.institution_id,
            content="虚构评论治理来源原文",
            rating=2,
            is_visible=True,
        )
        db.session.add(source)
        relation = FriendRelation.query.filter(
            db.or_(
                db.and_(
                    FriendRelation.user_id == actor.id,
                    FriendRelation.friend_user_id == linked.id,
                ),
                db.and_(
                    FriendRelation.user_id == linked.id,
                    FriendRelation.friend_user_id == actor.id,
                ),
            )
        ).one()
        relation.activate()
        db.session.commit()
        admin_id = admin.id
        source_id = source.id
        actor_id = actor.id
        linked_id = linked.id
        linked_institution_id = linked_report.institution_id
        relation_id = relation.id

    for forbidden_headers in (actor_headers, institution_headers):
        denied = client.post(
            "/api/comments/moderation/sanctions",
            headers=forbidden_headers,
            json={
                "user_id": actor_id,
                "source_comment_id": source_id,
                "reason": "越权治理尝试",
                "duration_days": 7,
            },
        )
        assert denied.status_code == 403

    mismatch = client.post(
        "/api/comments/moderation/sanctions",
        headers=admin_headers,
        json={
            "user_id": linked_id,
            "source_comment_id": source_id,
            "reason": "不应把来源评论绑定到另一个用户",
            "duration_days": 7,
        },
    )
    assert mismatch.status_code == 409
    assert mismatch.get_json()["code"] == "COMMENT_SANCTION_SUBJECT_MISMATCH"

    sanctioned = client.post(
        "/api/comments/moderation/sanctions",
        headers=admin_headers,
        json={
            "user_id": actor_id,
            "source_comment_id": source_id,
            "reason": "虚构恶意言论验收原因",
            "duration_days": 7,
        },
    )
    assert sanctioned.status_code == 201, sanctioned.get_json()
    sanction_id = sanctioned.get_json()["item"]["id"]
    with app.app_context():
        source = db.session.get(Comment, source_id)
        assert source.content == "虚构评论治理来源原文"
        assert source.is_visible is False
        assert source.hidden_reason == "虚构恶意言论验收原因"
        assert source.moderated_by_user_id == admin_id

    banned = client.post(
        "/api/comments",
        headers=actor_headers,
        json={
            "institution_id": linked_institution_id,
            "content": "该评论必须被当前账号禁言拦截",
            "rating": 5,
        },
    )
    assert banned.status_code == 403
    assert banned.get_json()["code"] == "COMMENT_BANNED"

    switched = client.post(
        f"/api/friends/{relation_id}/switch-session",
        headers=actor_headers,
    )
    assert switched.status_code == 200, switched.get_json()
    effective_headers = {
        "Authorization": f"Bearer {switched.get_json()['access_token']}"
    }
    allowed = client.post(
        "/api/comments",
        headers=effective_headers,
        json={
            "institution_id": linked_institution_id,
            "content": "虚构关联账号独立评论权限验收",
            "rating": 4,
        },
    )
    assert allowed.status_code == 201, allowed.get_json()
    assert allowed.get_json()["item"]["user_id"] == linked_id

    appeal = client.post(
        "/api/comments/appeals",
        headers=actor_headers,
        json={"sanction_id": sanction_id, "content": "虚构申诉说明"},
    )
    assert appeal.status_code == 201, appeal.get_json()
    appeal_id = appeal.get_json()["item"]["id"]
    repeated = client.post(
        "/api/comments/appeals",
        headers=actor_headers,
        json={"sanction_id": sanction_id, "content": "重复申诉"},
    )
    assert repeated.status_code == 409
    assert repeated.get_json()["code"] == "COMMENT_APPEAL_ALREADY_SUBMITTED"
    rejected = client.post(
        f"/api/comments/appeals/{appeal_id}/reject",
        headers=admin_headers,
        json={"review_note": "虚构审核后维持原处罚"},
    )
    assert rejected.status_code == 200, rejected.get_json()

    moderation = client.get(
        "/api/comments/moderation",
        headers=admin_headers,
    )
    rendered = next(
        row for row in moderation.get_json()["items"] if row["id"] == source_id
    )
    assert rendered["moderated_by"]["username"] == "admin"
    assert rendered["hidden_reason"] == "虚构恶意言论验收原因"
    with app.app_context():
        sanction = db.session.get(CommentSanction, sanction_id)
        assert sanction.status == "active"
        assert db.session.get(CommentAppeal, appeal_id).status == "rejected"
        audit = DelegatedActionAudit.query.filter_by(
            actor_user_id=actor_id,
            subject_user_id=linked_id,
            path="/api/comments",
            status_code=201,
        ).one()
        assert audit.outcome == "success"


def test_expired_comment_sanction_is_automatically_inactive(app, client):
    headers = register_completed(client, "expired_sanction_v12")
    with app.app_context():
        user = User.query.filter_by(username="expired_sanction_v12").one()
        admin = User.query.filter_by(username="admin").one()
        sanction = CommentSanction(
            user_id=user.id,
            reason="虚构已到期禁言",
            duration_days=7,
            status="active",
            starts_at=datetime.now(timezone.utc) - timedelta(days=8),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            created_by_admin_id=admin.id,
        )
        db.session.add(sanction)
        db.session.commit()
        sanction_id = sanction.id

    status = client.get("/api/comments/mine/sanction", headers=headers)
    assert status.status_code == 200
    assert status.get_json()["has_active_sanction"] is False
    with app.app_context():
        assert db.session.get(CommentSanction, sanction_id).status == "expired"


def test_comment_moderation_filters_before_pagination_and_returns_queue_counts(app, client):
    admin_headers = login(client, "admin", "admin123")
    with app.app_context():
        user = User.query.filter_by(username="test1").one()
        organization = Organization(name="虚构评论分页测试机构集团")
        institution = Institution(
            organization=organization,
            name=organization.name,
            branch_name="虚构评论分页测试分院",
            address="虚构测试地址 1 号",
            district="虚构测试区",
        )
        db.session.add_all([organization, institution])
        db.session.flush()

        comments = []
        for index in range(7):
            comments.append(Comment(
                user_id=user.id,
                institution_id=institution.id,
                content=f"虚构待审核评论 {index}",
                rating=3,
                is_visible=False,
            ))
        for index in range(3):
            comments.append(Comment(
                user_id=user.id,
                institution_id=institution.id,
                content=f"虚构已隐藏评论 {index}",
                rating=2,
                is_visible=False,
                hidden_reason="虚构审核隐藏原因",
            ))
        for index in range(4):
            comments.append(Comment(
                user_id=user.id,
                institution_id=institution.id,
                content=f"虚构已公开评论 {index}",
                rating=5,
                is_visible=True,
            ))
        db.session.add_all(comments)
        db.session.flush()
        reply_statuses = ["pending"] * 4 + ["approved"] * 2 + ["rejected"]
        db.session.add_all([
            CommentReply(
                comment_id=comments[index].id,
                institution_id=institution.id,
                content=f"虚构机构回复 {index}",
                status=status,
            )
            for index, status in enumerate(reply_statuses)
        ])
        db.session.commit()
        institution_id = institution.id

    pending_comments = client.get(
        "/api/comments/moderation",
        headers=admin_headers,
        query_string={
            "institution_id": institution_id,
            "queue": "comments",
            "page": 2,
            "page_size": 3,
        },
    )
    assert pending_comments.status_code == 200, pending_comments.get_json()
    payload = pending_comments.get_json()
    assert payload["pagination"] == {
        "page": 2,
        "page_size": 3,
        "total": 7,
        "pages": 3,
    }
    assert len(payload["items"]) == 3
    assert all(not item["is_visible"] and not item["hidden_reason"] for item in payload["items"])
    assert payload["counts"] == {
        "comments_pending": 7,
        "replies_pending": 4,
        "all": 14,
    }
    assert payload["filters"] == {
        "queue": "comments",
        "comment_status": "pending",
        "reply_status": "all",
    }

    pending_replies = client.get(
        "/api/comments/moderation",
        headers=admin_headers,
        query_string={
            "institution_id": institution_id,
            "queue": "replies",
            "page_size": 2,
        },
    ).get_json()
    assert pending_replies["pagination"]["total"] == 4
    assert all(item["reply"]["status"] == "pending" for item in pending_replies["items"])

    hidden_comments = client.get(
        "/api/comments/moderation",
        headers=admin_headers,
        query_string={
            "institution_id": institution_id,
            "queue": "all",
            "comment_status": "hidden",
        },
    ).get_json()
    assert hidden_comments["pagination"]["total"] == 3
    assert all(item["hidden_reason"] for item in hidden_comments["items"])

    approved_replies = client.get(
        "/api/comments/moderation",
        headers=admin_headers,
        query_string={
            "institution_id": institution_id,
            "queue": "all",
            "reply_status": "approved",
        },
    ).get_json()
    assert approved_replies["pagination"]["total"] == 2
    assert all(item["reply"]["status"] == "approved" for item in approved_replies["items"])

    invalid = client.get(
        "/api/comments/moderation",
        headers=admin_headers,
        query_string={"queue": "unknown"},
    )
    assert invalid.status_code == 400


def test_comment_appeals_are_status_filtered_paginated_and_include_safe_user(app, client):
    admin_headers = login(client, "admin", "admin123")
    with app.app_context():
        CommentAppeal.query.delete()
        CommentSanction.query.delete()
        user = User.query.filter_by(username="test1").one()
        admin = User.query.filter_by(username="admin").one()
        appeal_statuses = ["pending"] * 5 + ["approved"] * 2 + ["rejected"]
        for index, appeal_status in enumerate(appeal_statuses):
            sanction = CommentSanction(
                user_id=user.id,
                reason=f"虚构分页禁言原因 {index}",
                duration_days=7,
                status="active",
                created_by_admin_id=admin.id,
            )
            db.session.add(sanction)
            db.session.flush()
            db.session.add(CommentAppeal(
                sanction_id=sanction.id,
                user_id=user.id,
                content=f"虚构分页申诉说明 {index}",
                status=appeal_status,
            ))
        db.session.commit()

    response = client.get(
        "/api/comments/appeals",
        headers=admin_headers,
        query_string={"status": "pending", "page": 2, "page_size": 2},
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload["pagination"] == {
        "page": 2,
        "page_size": 2,
        "total": 5,
        "pages": 3,
    }
    assert payload["counts"] == {
        "pending": 5,
        "approved": 2,
        "rejected": 1,
        "all": 8,
    }
    assert payload["filters"] == {"status": "pending"}
    assert len(payload["items"]) == 2
    assert all(item["status"] == "pending" for item in payload["items"])
    assert all(item["user"] == {"id": item["user_id"], "username": "test1"} for item in payload["items"])
    assert all(item["sanction"]["user"]["username"] == "test1" for item in payload["items"])

    invalid = client.get(
        "/api/comments/appeals",
        headers=admin_headers,
        query_string={"status": "unknown"},
    )
    assert invalid.status_code == 400


def test_complaint_access_and_escalation_lock_institution(app, client):
    complainant_headers = login(client, "test1")
    other_headers = login(client, "test2")
    with app.app_context():
        complainant = User.query.filter_by(username="test1").one()
        complaint = AppointmentComplaint.query.filter_by(
            complainant_user_id=complainant.id,
            status="institution_pending",
        ).first()
        assert complaint is not None
        complaint_id = complaint.id
        institution_id = complaint.institution_id
        correct_manager = complaint.institution.administrator.username
        wrong_institution = Institution.query.filter(
            Institution.id != institution_id,
            Institution.is_active.is_(True),
        ).first()
        wrong_manager = wrong_institution.administrator.username

    assert client.get(
        f"/api/complaints/{complaint_id}",
        headers=other_headers,
    ).status_code == 404
    assert client.post(
        f"/api/org/complaints/{complaint_id}/reply",
        headers=login(client, wrong_manager),
        json={"content": "越权机构不应看到或回复"},
    ).status_code == 404

    before = client.get(
        f"/api/complaints/{complaint_id}",
        headers=complainant_headers,
    )
    serialized = str(before.get_json())
    for forbidden in (
        "health_id",
        "allergy_history",
        "medical_history",
        "indicator",
        "ocr_diagnostics",
    ):
        assert forbidden not in serialized

    escalated = client.post(
        f"/api/complaints/{complaint_id}/escalate",
        headers=complainant_headers,
        json={"reason": "虚构：提交后立即申请平台介入"},
    )
    assert escalated.status_code == 200, escalated.get_json()
    assert escalated.get_json()["item"]["status"] == "platform_pending"
    locked = client.post(
        f"/api/org/complaints/{complaint_id}/reply",
        headers=login(client, correct_manager),
        json={"content": "平台介入后机构不应继续处理"},
    )
    assert locked.status_code == 409
    assert locked.get_json()["code"] == "COMPLAINT_STATE_CONFLICT"
    assert client.post(
        f"/api/complaints/{complaint_id}/escalate",
        headers=other_headers,
        json={"reason": "越权用户升级"},
    ).status_code == 404
