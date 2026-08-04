import json
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import (
    Appointment,
    AppointmentComplaint,
    AppointmentEvent,
    ComplaintMessage,
    IndicatorDict,
    Institution,
    InstitutionAudienceInsightCache,
    InstitutionReport,
    NotificationOutbox,
    Package,
    User,
    UserNotification,
    WaitlistSubscription,
)


PASSWORD = "Shuhealthdoc！"
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


def login(client, username, password=PASSWORD):
    response = client.post(
        "/api/auth/login",
        json=client.login_payload(username, password),
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def _appointment(institution, package, user, appointment_date, status):
    return Appointment(
        user_id=user.id,
        institution_id=institution.id,
        package_id=package.id,
        package_version_id=package.current_version_id,
        booked_by_user_id=user.id,
        appointment_date=appointment_date,
        active_date_key=None,
        status=status,
        user_name_snapshot=user.real_name or "未实名测试用户",
        user_health_id_snapshot=user.health_id,
        user_birth_date_snapshot=user.birth_date,
        user_gender_snapshot=user.gender,
        user_contact_snapshot=user.email,
        package_name_snapshot=package.name,
        package_price_snapshot=package.price,
    )


def test_institution_deactivation_exposes_all_canonical_blockers(app, client):
    business_today = datetime.now(BUSINESS_TZ).date()
    with app.app_context():
        institution = Institution.query.order_by(Institution.id).all()[3]
        manager = institution.administrator
        package = next(row for row in institution.packages if row.is_active)
        user = User.query.filter_by(username="test6").first()

        future = _appointment(
            institution,
            package,
            user,
            business_today,
            "unfulfilled",
        )
        overdue = _appointment(
            institution,
            package,
            user,
            business_today - timedelta(days=2),
            "unfulfilled",
        )
        arrived = _appointment(
            institution,
            package,
            user,
            business_today - timedelta(days=1),
            "awaiting_report",
        )
        db.session.add_all((future, overdue, arrived))
        db.session.flush()
        db.session.add(AppointmentComplaint(
            appointment_id=future.id,
            institution_id=institution.id,
            complainant_user_id=user.id,
            complainant_username_snapshot=user.username,
            category="service",
            content="注销门禁未解决投诉测试",
            status="institution_pending",
        ))
        db.session.add(InstitutionReport(
            institution_id=institution.id,
            created_by_user_id=manager.id,
            created_by_username_snapshot=manager.username,
            subject_name_snapshot=user.real_name or "未实名测试用户",
            subject_health_id=user.health_id,
            exam_date=business_today - timedelta(days=3),
            package_id=package.id,
            package_version_id=package.current_version_id,
            matched_user_id=user.id,
            status="draft",
        ))
        manager_username = manager.username
        db.session.commit()

    headers = login(client, manager_username)
    response = client.get(
        "/api/org/account/deactivation-check",
        headers=headers,
    )
    assert response.status_code == 200
    item = response.get_json()["item"]
    assert {
        key: item[key]
        for key in (
            "future_effective_appointments",
            "arrived_unfinished_reports",
            "draft_or_pending_reports",
            "unresolved_complaints",
            "other_upload_tasks",
        )
    } == {
        "future_effective_appointments": 1,
        "arrived_unfinished_reports": 1,
        "draft_or_pending_reports": 1,
        "unresolved_complaints": 1,
        "other_upload_tasks": 2,
    }
    assert item["can_deactivate"] is False

    blocked = client.post(
        "/api/org/account/deactivate",
        headers=headers,
        json={"confirm": True, "current_password": PASSWORD},
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["code"] == "INSTITUTION_DEACTIVATION_BLOCKED"
    assert blocked.get_json()["blockers"]["unresolved_complaints"] == 1


def test_institution_deactivation_invalidates_waitlist_and_notifies(app, client):
    with app.app_context():
        institution = Institution.query.order_by(Institution.id).all()[3]
        manager = institution.administrator
        package = next(row for row in institution.packages if row.is_active)
        subscriber = User.query.filter_by(username="test1").first()
        subscription = WaitlistSubscription(
            subscriber_user_id=subscriber.id,
            institution_id=institution.id,
            package_id=package.id,
            package_version_id=package.current_version_id,
            appointment_date=datetime.now(BUSINESS_TZ).date() + timedelta(days=7),
            party_size=1,
            notification_email=subscriber.email,
            status="active",
        )
        db.session.add(subscription)
        db.session.commit()
        institution_id = institution.id
        manager_id = manager.id
        manager_username = manager.username
        subscriber_id = subscriber.id
        subscription_id = subscription.id

    headers = login(client, manager_username)
    precheck = client.get(
        "/api/org/account/deactivation-check",
        headers=headers,
    ).get_json()["item"]
    assert precheck["can_deactivate"] is True
    assert precheck["active_waitlist_subscriptions"] == 1

    response = client.post(
        "/api/org/account/deactivate",
        headers=headers,
        json={"confirm": True, "current_password": PASSWORD},
    )
    assert response.status_code == 200
    assert response.get_json()["invalidated_waitlist_subscriptions"] == 1

    with app.app_context():
        subscription = db.session.get(WaitlistSubscription, subscription_id)
        assert subscription.status == "invalid"
        assert subscription.closed_at is not None
        assert db.session.get(Institution, institution_id).is_active is False
        assert db.session.get(User, manager_id).is_active is False
        assert UserNotification.query.filter_by(
            user_id=subscriber_id,
            event_type="waitlist_institution_deactivated",
        ).count() == 1
        outboxes = NotificationOutbox.query.filter_by(
            event_type="waitlist_institution_deactivated",
        ).all()
        assert len(outboxes) == 1
        assert outboxes[0].recipient
        assert "password" not in json.dumps(
            outboxes[0].payload,
            ensure_ascii=False,
        ).lower()


def _create_reviewable_report(client, user_headers, org_headers):
    with client.application.app_context():
        user = User.query.filter_by(username="test3").first()
        institution = Institution.query.order_by(Institution.id).first()
        package = next(row for row in institution.packages if row.is_active)
        indicator = IndicatorDict.query.filter_by(code="HR").first()
        institution_id = institution.id
        package_id = package.id
        indicator_id = indicator.id
        user_id = user.id
        business_today = datetime.now(BUSINESS_TZ).date()
        occupied_dates = {
            row.appointment_date
            for row in Appointment.query.filter(
                Appointment.user_id == user.id,
                Appointment.status.in_((
                    "unfulfilled",
                    "awaiting_report",
                    "fulfilled",
                )),
            )
        }
        appointment_date = next(
            business_today + timedelta(days=offset)
            for offset in range(1, 31)
            if business_today + timedelta(days=offset) not in occupied_dates
        )
    appointment = client.post(
        "/api/appointments",
        headers=user_headers,
        json={
            "institution_id": institution_id,
            "package_id": package_id,
            "appointment_date": appointment_date.isoformat(),
            "height_cm": 170,
            "weight_kg": 65,
            "notice_confirmed": True,
        },
    )
    assert appointment.status_code == 201
    appointment_id = appointment.get_json()["item"]["id"]
    assert client.post(
        f"/api/payment-orders/{appointment.get_json()['payment_order']['id']}/pay",
        headers=user_headers,
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
    assert report.status_code == 201
    report_id = report.get_json()["item"]["id"]
    indicator_response = client.post(
        f"/api/org/reports/{report_id}/indicators",
        headers=org_headers,
        json={"indicator_dict_id": indicator_id, "value": "72"},
    )
    assert indicator_response.status_code == 201
    domain_id = indicator_response.get_json()["item"]["display_domain_id"]
    conclusion = client.post(
        f"/api/org/health-data/{report_id}/text-results",
        headers=org_headers,
        json={
            "health_domain_id": domain_id,
            "title": "心血管检查结论",
            "body": "复核并发验收报告结论完整。",
        },
    )
    assert conclusion.status_code == 201
    return report_id, appointment_id, user_id


def test_report_review_replay_has_conflict_code_and_single_side_effects(app, client):
    user_headers = login(client, "test3")
    org_headers = login(client, "institution1_staff1")
    report_id, appointment_id, owner_id = _create_reviewable_report(
        client,
        user_headers,
        org_headers,
    )
    submitted = client.post(
        f"/api/org/reports/{report_id}/submit-review",
        headers=org_headers,
        json={"upload_doctor_name": "张医生"},
    )
    assert submitted.status_code == 200
    duplicate_submit = client.post(
        f"/api/org/reports/{report_id}/submit-review",
        headers=org_headers,
        json={"upload_doctor_name": "张医生"},
    )
    assert duplicate_submit.status_code == 409
    assert duplicate_submit.get_json()["code"] == "REPORT_STATE_CONFLICT"

    reviewed = client.post(
        f"/api/org/reports/{report_id}/review",
        headers=org_headers,
        json={"review_doctor_name": "王医生"},
    )
    assert reviewed.status_code == 200
    duplicate_review = client.post(
        f"/api/org/reports/{report_id}/review",
        headers=org_headers,
        json={"review_doctor_name": "王医生"},
    )
    assert duplicate_review.status_code == 409
    assert duplicate_review.get_json()["code"] == "REPORT_STATE_CONFLICT"

    with app.app_context():
        report = db.session.get(InstitutionReport, report_id)
        assert report.status == "published"
        assert AppointmentEvent.query.filter_by(
            appointment_id=appointment_id,
            event_type="report_published",
        ).count() == 1
        assert UserNotification.query.filter_by(
            user_id=owner_id,
            idempotency_key=f"report:{report_id}:published",
        ).count() == 1
        assert NotificationOutbox.query.filter_by(
            idempotency_key=(
                f"user:{owner_id}:report:{report_id}:published:email"
            ),
        ).count() == 1


def test_complaint_messages_are_append_only_and_state_conflicts_are_coded(
    app,
    client,
):
    with app.app_context():
        user = User.query.filter_by(username="test1").first()
        item = AppointmentComplaint.query.filter_by(
            complainant_user_id=user.id,
            status="institution_pending",
        ).first()
        complaint_id = item.id
        manager_username = item.institution.administrator.username

    user_headers = login(client, "test1")
    org_headers = login(client, manager_username)
    admin_headers = login(client, "admin", "admin123")

    initial = client.get(
        f"/api/complaints/{complaint_id}",
        headers=user_headers,
    ).get_json()["item"]
    assert [row["sender_role"] for row in initial["messages"]] == ["user"]

    institution_reply = client.post(
        f"/api/org/complaints/{complaint_id}/reply",
        headers=org_headers,
        json={"content": "机构已核查并给出改进处理方案。"},
    )
    assert institution_reply.status_code == 200
    duplicate_institution_reply = client.post(
        f"/api/org/complaints/{complaint_id}/reply",
        headers=org_headers,
        json={"content": "重复回复不应追加。"},
    )
    assert duplicate_institution_reply.status_code == 409
    assert (
        duplicate_institution_reply.get_json()["code"]
        == "COMPLAINT_STATE_CONFLICT"
    )

    escalated = client.post(
        f"/api/complaints/{complaint_id}/escalate",
        headers=user_headers,
        json={"reason": "对机构方案仍有疑问，请平台核验。"},
    )
    assert escalated.status_code == 200
    assert client.post(
        f"/api/admin/complaints/{complaint_id}/start",
        headers=admin_headers,
    ).status_code == 200
    admin_reply = client.post(
        f"/api/admin/complaints/{complaint_id}/reply",
        headers=admin_headers,
        json={"content": "平台已核验记录并给出最终处理意见。"},
    )
    assert admin_reply.status_code == 200
    assert client.post(
        f"/api/admin/complaints/{complaint_id}/resolve",
        headers=admin_headers,
    ).status_code == 200
    duplicate_admin_reply = client.post(
        f"/api/admin/complaints/{complaint_id}/reply",
        headers=admin_headers,
        json={"content": "已关闭后不应追加。"},
    )
    assert duplicate_admin_reply.status_code == 409
    assert duplicate_admin_reply.get_json()["code"] == "COMPLAINT_STATE_CONFLICT"

    final_item = client.get(
        f"/api/complaints/{complaint_id}",
        headers=user_headers,
    ).get_json()["item"]
    assert [row["sender_role"] for row in final_item["messages"]] == [
        "user",
        "institution_admin",
        "user",
        "admin",
    ]
    assert final_item["status"] == "resolved"
    with app.app_context():
        assert ComplaintMessage.query.filter_by(
            complaint_id=complaint_id,
        ).count() == 4


def test_audience_insight_period_privacy_and_digest_invalidation(app, client):
    headers = login(client, "institution1_staff1")
    invalid = client.get(
        "/api/org/audience-insights?scope=organization&period_days=60",
        headers=headers,
    )
    assert invalid.status_code == 400

    first = client.get(
        "/api/org/audience-insights?scope=organization&period_days=0",
        headers=headers,
    )
    assert first.status_code == 200
    first_payload = first.get_json()
    assert first_payload["ai"]["cache_hit"] is False
    aggregate = first_payload["aggregate"]
    assert set(aggregate) == {
        "scope",
        "period_days",
        "period_start",
        "period_end",
        "report_count",
        "unique_user_count",
        "gender_distribution",
        "age_distribution",
        "package_ranking",
        "package_catalog",
        "branch_distribution",
    }
    assert aggregate["scope"] == "organization"
    serialized = json.dumps(first_payload, ensure_ascii=False)
    with app.app_context():
        health_ids = [
            row.health_id
            for row in User.query.filter_by(role="user").all()
            if row.health_id
        ]
        cache = InstitutionAudienceInsightCache.query.filter_by(
            scope_type="organization",
            period_key="all",
        ).one()
        first_digest = cache.data_digest
        package = Package.query.filter_by(is_active=True).first()
        package.price = Decimal(package.price) + Decimal("1.00")
        db.session.commit()
    assert not any(health_id in serialized for health_id in health_ids)

    changed = client.get(
        "/api/org/audience-insights?scope=organization&period_days=0",
        headers=headers,
    )
    assert changed.status_code == 200
    assert changed.get_json()["ai"]["cache_hit"] is False
    with app.app_context():
        cache = InstitutionAudienceInsightCache.query.filter_by(
            scope_type="organization",
            period_key="all",
        ).one()
        assert cache.data_digest != first_digest

    cached = client.get(
        "/api/org/audience-insights?scope=organization&period_days=0",
        headers=headers,
    )
    assert cached.status_code == 200
    assert cached.get_json()["ai"]["cache_hit"] is True
