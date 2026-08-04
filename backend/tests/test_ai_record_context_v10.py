"""Independent contract tests for v10 conversational record context.

These tests intentionally exercise the HTTP boundary rather than private route
helpers.  They are kept separate from the legacy consent tests because the v10
contract makes consent informational and keeps the selected record context
alive for the whole conversation.
"""

from datetime import date

from app.ai.service import AiCompletion
from app.extensions import db
from app.models import (
    FriendRelation,
    HealthIndicator,
    HealthRecord,
    IndicatorDict,
    User,
)


PASSWORD = "Shuhealthdoc！"


def login(client, username, password=PASSWORD):
    response = client.post(
        "/api/auth/login",
        json=client.login_payload(username, password),
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def published_records(app, username):
    with app.app_context():
        user = User.query.filter_by(username=username).one()
        rows = (
            HealthRecord.query.filter_by(owner_id=user.id, status="published")
            .filter(HealthRecord.indicators.any())
            .order_by(HealthRecord.exam_date.asc(), HealthRecord.id.asc())
            .all()
        )
        return [
            {
                "id": row.id,
                "owner_id": row.owner_id,
                "exam_date": row.exam_date.isoformat(),
            }
            for row in rows
        ]


def published_report_ids_with_indicator(app, username, code):
    with app.app_context():
        user = User.query.filter_by(username=username).one()
        definition = IndicatorDict.query.filter_by(code=code).one()
        return {
            row.id
            for row in HealthRecord.query.filter_by(
                owner_id=user.id, status="published"
            ).all()
            if any(
                item.indicator_dict_id == definition.id for item in row.indicators
            )
        }


def ensure_indicator_history(app, username, code, *, minimum=2):
    """Make focused contract tests independent from whichever demo seed is active."""
    with app.app_context():
        user = User.query.filter_by(username=username).one()
        definition = IndicatorDict.query.filter_by(code=code).one()
        reports = (
            HealthRecord.query.filter_by(owner_id=user.id, status="published")
            .filter(HealthRecord.indicators.any())
            .order_by(HealthRecord.exam_date.asc(), HealthRecord.id.asc())
            .all()
        )
        assert len(reports) >= minimum
        existing_ids = {
            item.report_id
            for item in HealthIndicator.query.filter_by(
                indicator_dict_id=definition.id
            ).filter(HealthIndicator.report_id.in_([row.id for row in reports]))
        }
        for index, report in enumerate(reports):
            if len(existing_ids) >= minimum:
                break
            if report.id in existing_ids:
                continue
            db.session.add(
                HealthIndicator(
                    report_id=report.id,
                    indicator_dict_id=definition.id,
                    value=str(round(2.6 + index * 0.08, 2)),
                    result_status="normal",
                    input_source="manual",
                    mapping_status="confirmed",
                )
            )
            existing_ids.add(report.id)
        db.session.commit()


def unauthorized_published_record(app, viewer_username):
    with app.app_context():
        viewer = User.query.filter_by(username=viewer_username).one()
        authorized = {
            viewer.id,
            *(
                row[0]
                for row in db.session.query(FriendRelation.friend_user_id)
                .filter_by(user_id=viewer.id, auth_status=True)
                .all()
            ),
        }
        record = (
            HealthRecord.query.filter(
                HealthRecord.owner_id.notin_(authorized),
                HealthRecord.status == "published",
                HealthRecord.indicators.any(),
            )
            .order_by(HealthRecord.id.asc())
            .first()
        )
        if record is None:
            relation = FriendRelation.query.filter_by(
                user_id=viewer.id, auth_status=True
            ).first()
            assert relation is not None
            relation.auth_status = False
            db.session.flush()
            record = (
                HealthRecord.query.filter_by(
                    owner_id=relation.friend_user_id,
                    status="published",
                )
                .filter(HealthRecord.indicators.any())
                .first()
            )
            db.session.commit()
        assert record is not None
        return {
            "id": record.id,
            "owner_id": record.owner_id,
            "exam_date": record.exam_date.isoformat(),
        }


def chat(client, headers, message, **extra):
    payload = {"message": message, "history": [], **extra}
    response = client.post("/api/ai/chat", headers=headers, json=payload)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def assert_resolution(payload, *, owner_id, scope_mode=None):
    resolution = payload["record_resolution"]
    assert resolution["owner"]["id"] == owner_id
    assert resolution["record_count"] >= 1
    assert resolution["date_range"]["start"]
    assert resolution["date_range"]["end"]
    assert isinstance(resolution["records"], list)
    assert payload["next_active_record_context"]["owner_id"] == owner_id
    if scope_mode:
        assert resolution["scope_mode"] == scope_mode
        assert payload["next_active_record_context"]["scope_mode"] == scope_mode
    return resolution


def test_latest_report_is_semantically_selected_and_context_is_reusable(app, client):
    headers = login(client, "test1")
    records = published_records(app, "test1")
    latest = records[-1]

    first = chat(client, headers, "帮我分析我上一次的体检报告")
    resolution = assert_resolution(
        first, owner_id=latest["owner_id"], scope_mode="selected_records"
    )
    assert resolution["record_count"] == 1
    assert resolution["records"][0]["id"] == latest["id"]
    assert first["record_resolution"]["source"] == "semantic"

    # The second turn supplies the server-issued active context, exactly as
    # the frontend does after persisting the first answer.
    second = chat(
        client,
        headers,
        "具体分析这个报告里的 LDL",
        active_record_context=first["next_active_record_context"],
    )
    second_resolution = assert_resolution(
        second, owner_id=latest["owner_id"], scope_mode="selected_records"
    )
    assert second_resolution["records"][0]["id"] == latest["id"]
    assert second["record_resolution"]["source"] in {"inherited", "semantic"}


def test_explicit_year_switches_owner_scope_without_mixing_members(app, client):
    headers = login(client, "test1")
    rows = published_records(app, "test1")
    year = date.fromisoformat(rows[len(rows) // 2]["exam_date"]).year
    expected = [row for row in rows if row["exam_date"].startswith(f"{year}-")]
    assert expected

    payload = chat(client, headers, f"请分析我 {year} 年的体检报告")
    resolution = assert_resolution(
        payload, owner_id=expected[0]["owner_id"], scope_mode="selected_records"
    )
    assert {item["id"] for item in resolution["records"]} == {
        item["id"] for item in expected
    }
    assert all(item["exam_date"].startswith(f"{year}-") for item in resolution["records"])


def test_trend_follow_up_expands_same_owner_history(app, client):
    headers = login(client, "test1")
    ensure_indicator_history(app, "test1", "LDL")
    first = chat(client, headers, "分析我上一次的体检报告")
    context = dict(first["next_active_record_context"])
    context["indicator_codes"] = ["LDL"]

    payload = chat(
        client,
        headers,
        "看看这个指标最近几年的趋势",
        active_record_context=context,
    )
    resolution = assert_resolution(
        payload, owner_id=context["owner_id"], scope_mode="indicator_history"
    )
    assert resolution["indicators"] == ["LDL"]
    expected_ids = published_report_ids_with_indicator(app, "test1", "LDL")
    assert expected_ids
    assert resolution["record_count"] == len(expected_ids)
    assert {item["id"] for item in resolution["records"]}.issubset(expected_ids)


def test_ldl_aliases_are_normalized_to_canonical_indicator_code(app, client):
    headers = login(client, "test1")
    ensure_indicator_history(app, "test1", "LDL")
    first = chat(client, headers, "分析我上一次的体检报告")
    context = first["next_active_record_context"]

    for alias in ("LDL-C", "LDL_C"):
        payload = chat(
            client,
            headers,
            f"分析 {alias} 最近几年的趋势",
            active_record_context=context,
        )
        assert payload["record_resolution"]["scope_mode"] == "indicator_history"
        assert payload["record_resolution"]["indicators"] == ["LDL"]


def test_manual_context_does_not_require_repeated_consent_and_all_history_is_bounded(
    app, client
):
    headers = login(client, "test1")
    rows = published_records(app, "test1")
    owner_id = rows[0]["owner_id"]
    anchor = rows[-1]["id"]

    payload = chat(
        client,
        headers,
        "继续解释这份报告",
        active_record_context={
            "owner_id": owner_id,
            "anchor_record_ids": [anchor],
            "scope_mode": "selected_records",
            "indicator_codes": [],
        },
    )
    assert payload["record_resolution"]["source"] in {"manual", "inherited"}
    assert payload["selected_record_ids"] == [anchor]

    all_history = chat(
        client,
        headers,
        "总结我的全部历史报告",
        active_record_context={
            "owner_id": owner_id,
            "anchor_record_ids": [anchor],
            "scope_mode": "all_confirmed",
            "indicator_codes": [],
        },
    )
    all_resolution = assert_resolution(
        all_history, owner_id=owner_id, scope_mode="all_confirmed"
    )
    assert all_resolution["record_count"] == len(rows)
    if len(rows) > 10:
        assert all_resolution.get("records_truncated") is True
    else:
        assert all_resolution.get("records_truncated") in {None, False}
    assert len(str(all_history)) < 120_000


def test_record_context_rejects_unauthorized_and_mixed_owners(app, client):
    headers = login(client, "test1")
    own = published_records(app, "test1")[0]
    with app.app_context():
        viewer = User.query.filter_by(username="test1").one()
        relation = FriendRelation.query.filter_by(
            user_id=viewer.id, auth_status=True
        ).first()
        assert relation is not None
        authorized_other = (
            HealthRecord.query.filter_by(
                owner_id=relation.friend_user_id, status="published"
            )
            .filter(HealthRecord.indicators.any())
            .first()
        )
        assert authorized_other is not None
        authorized_other_id = authorized_other.id

    mixed = client.post(
        "/api/ai/chat",
        headers=headers,
        json={
            "message": "比较这两份报告",
            "selected_record_ids": [own["id"], authorized_other_id],
        },
    )
    assert mixed.status_code == 404
    assert mixed.get_json()["error"]["code"] == "record_unavailable"

    other = unauthorized_published_record(app, "test1")
    forbidden = client.post(
        "/api/ai/chat",
        headers=headers,
        json={
            "message": "继续分析",
            "active_record_context": {
                "owner_id": other["owner_id"],
                "anchor_record_ids": [other["id"]],
                "scope_mode": "selected_records",
                "indicator_codes": [],
            },
        },
    )
    assert forbidden.status_code == 404
    assert forbidden.get_json()["error"]["code"] == "record_unavailable"


def test_revoked_friend_context_is_revalidated_each_turn(app, client):
    headers = login(client, "test1")
    with app.app_context():
        viewer = User.query.filter_by(username="test1").one()
        relation = FriendRelation.query.filter_by(
            user_id=viewer.id, auth_status=True
        ).first()
        assert relation is not None
        friend_id = relation.friend_user_id
        friend_record = (
            HealthRecord.query.filter_by(owner_id=friend_id, status="published")
            .filter(HealthRecord.indicators.any())
            .order_by(HealthRecord.exam_date.desc(), HealthRecord.id.desc())
            .first()
        )
        assert friend_record is not None
        context = {
            "owner_id": friend_id,
            "anchor_record_ids": [friend_record.id],
            "scope_mode": "selected_records",
            "indicator_codes": [],
        }
        relation.auth_status = False
        db.session.commit()

    response = client.post(
        "/api/ai/chat",
        headers=headers,
        json={"message": "继续分析这份亲友报告", "active_record_context": context},
    )
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "record_unavailable"


def test_model_context_never_exposes_identity_or_history_sensitive_fields(
    app, client, monkeypatch
):
    headers = login(client, "test1")
    with app.app_context():
        user = User.query.filter_by(username="test1").one()
        health_id = user.health_id
        user.phone = "13999998888"
        user.allergy_history = "MODEL_SECRET_ALLERGY"
        user.medical_history = "MODEL_SECRET_MEDICAL"
        db.session.commit()

    captured_messages = []

    class CapturingClient:
        model = "capture"

        def complete(self, messages, *, json_output=False, max_tokens=1200):
            captured_messages.extend(messages)
            return AiCompletion(
                content='{"decision":"answer","answer":"已完成分析"}',
                usage={"total_tokens": 1},
            )

    monkeypatch.setattr(
        "app.ai.routes.get_ai_client",
        lambda _config: CapturingClient(),
    )
    payload = chat(client, headers, "分析我上一次的体检报告")
    model_input = "\n".join(item["content"] for item in captured_messages)
    assert health_id not in model_input
    assert "13999998888" not in model_input
    assert "MODEL_SECRET_ALLERGY" not in model_input
    assert "MODEL_SECRET_MEDICAL" not in model_input
    assert "allergy_history" not in model_input
    assert "medical_history" not in model_input
    resolution = payload["record_resolution"]
    assert set(resolution["records"][0]) >= {"id", "exam_date", "institution_name"}
    assert "owner_name" not in resolution["records"][0]


def test_streamed_latest_report_returns_done_resolution_without_detached_error(
    app, client
):
    headers = login(client, "test1")
    response = client.post(
        "/api/ai/chat/stream",
        headers=headers,
        json={"message": "帮我分析我上一次的体检报告", "history": []},
        buffered=True,
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "event: error" not in body
    assert "DetachedInstanceError" not in body
    assert "event: done" in body
    assert '"record_resolution"' in body
    assert '"next_active_record_context"' in body
