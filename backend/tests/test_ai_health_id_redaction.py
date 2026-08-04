import json

from app.ai.service import AiCompletion
from app.services.sensitive_data import (
    HEALTH_ID_REDACTION,
    redact_health_identity_codes,
)


RAW_HEALTH_ID = "HID-8K3M2Q7A"
LOWER_HEALTH_ID = "hid-5r9t4w2c"


def test_health_id_redaction_is_recursive_case_insensitive_and_exact():
    value = {
        RAW_HEALTH_ID: [
            f"前缀 {RAW_HEALTH_ID} 后缀",
            {"nested": LOWER_HEALTH_ID},
        ]
    }

    redacted = redact_health_identity_codes(value)
    serialized = json.dumps(redacted, ensure_ascii=False)

    assert RAW_HEALTH_ID not in serialized
    assert LOWER_HEALTH_ID not in serialized
    assert serialized.count(HEALTH_ID_REDACTION) == 3
    for invalid in (
        "HID-8K3M2Q7",
        "HID-8K3M2Q1A",
        "HID-8K3M2QIA",
    ):
        assert redact_health_identity_codes(invalid) == invalid
    for wrapped in (
        "XHID-8K3M2Q7AY",
        "HID-8K3M2Q7A9",
        "中文HID-8K3M2Q7A中文",
    ):
        assert RAW_HEALTH_ID not in redact_health_identity_codes(wrapped)
        assert HEALTH_ID_REDACTION in redact_health_identity_codes(wrapped)


def test_json_chat_redacts_before_history_compaction_model_and_response(
    app, client, monkeypatch
):
    captured = {}

    class CapturingClient:
        model = "capture-json"

        def complete(self, messages, *, json_output=False, max_tokens=1200):
            captured["messages"] = messages
            return AiCompletion(
                content=f"模型返回 {RAW_HEALTH_ID} / {LOWER_HEALTH_ID}",
                usage={"provider_note": RAW_HEALTH_ID},
            )

    monkeypatch.setattr(
        "app.ai.routes.get_ai_client", lambda _config: CapturingClient()
    )
    app.config["AI_MAX_HISTORY_MESSAGES"] = 2

    response = client.post(
        "/api/ai/chat",
        json={
            "message": f"请解释字符串 {RAW_HEALTH_ID}",
            "history": [
                {"role": "user", "content": f"之前输入 {LOWER_HEALTH_ID}"},
                {"role": "assistant", "content": f"之前回答 {RAW_HEALTH_ID}"},
            ],
            "summary": f"旧摘要 {LOWER_HEALTH_ID}",
        },
    )

    assert response.status_code == 200, response.get_json()
    model_input = json.dumps(captured["messages"], ensure_ascii=False)
    response_text = response.get_data(as_text=True)
    for raw_value in (RAW_HEALTH_ID, LOWER_HEALTH_ID):
        assert raw_value not in model_input
        assert raw_value not in response_text
    assert HEALTH_ID_REDACTION in model_input
    assert HEALTH_ID_REDACTION in response.get_json()["summary"]
    assert HEALTH_ID_REDACTION in response.get_json()["reply"]
    assert response.get_json()["compacted_count"] == 2


def test_stream_chat_redacts_split_provider_output_and_all_request_context(
    app, client, monkeypatch
):
    captured = {}

    class CapturingStreamClient:
        model = "capture-stream"

        def stream(self, messages, *, json_output=False, max_tokens=1200):
            captured["messages"] = messages
            yield "模型返回 HID-8K3", None
            yield "M2Q7A，完成", {"provider_note": LOWER_HEALTH_ID}

    monkeypatch.setattr(
        "app.ai.routes.get_ai_client", lambda _config: CapturingStreamClient()
    )

    response = client.post(
        "/api/ai/chat/stream",
        json={
            "message": f"解释 {LOWER_HEALTH_ID}",
            "history": [
                {"role": "user", "content": f"问题 {RAW_HEALTH_ID}"},
                {"role": "assistant", "content": f"回答 {LOWER_HEALTH_ID}"},
            ],
            "summary": f"摘要 {RAW_HEALTH_ID}",
        },
        buffered=True,
    )

    assert response.status_code == 200
    model_input = json.dumps(captured["messages"], ensure_ascii=False)
    stream_text = response.get_data(as_text=True)
    for raw_value in (RAW_HEALTH_ID, LOWER_HEALTH_ID):
        assert raw_value not in model_input
        assert raw_value not in stream_text
    assert HEALTH_ID_REDACTION in model_input
    assert HEALTH_ID_REDACTION in stream_text
    assert "event: done" in stream_text
