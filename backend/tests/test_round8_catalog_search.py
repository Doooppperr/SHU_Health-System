from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.ai.service import AiProviderError
from app.services.catalog_search import CatalogIntent


def _login(client, username="test1", password="Shuhealthdoc！"):
    response = client.post(
        "/api/auth/login",
        json=client.login_payload(username, password),
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def test_public_smart_search_recommends_female_package_organization(client):
    response = client.get(
        "/api/public/organizations",
        query_string={"q": "女性", "search_mode": "content"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["search"]["mode"] == "content"
    assert len(payload["search"]["suggestions"]) <= 8
    assert payload["items"][0]["name"] == "安沐女性与家庭健康中心"
    assert payload["items"][0]["matched_packages"]
    matched_names = {
        package["name"]
        for item in payload["items"]
        for branch in item["branches"]
        for package in branch["matched_packages"]
    }
    assert "女性年度基础关怀" in matched_names
    assert all("notification_email" not in branch for item in payload["items"] for branch in item["branches"])


def test_authenticated_smart_search_preserves_location_and_literal_safety(client):
    headers = _login(client)
    response = client.get(
        "/api/organizations",
        headers=headers,
        query_string={"q": "陆家嘴", "search_mode": "content"},
    )
    assert response.status_code == 200
    branches = [branch for item in response.get_json()["items"] for branch in item["branches"]]
    assert branches
    assert all("陆家嘴" in branch["branch_name"] for branch in branches)

    special = client.get(
        "/api/organizations",
        headers=headers,
        query_string={"q": "%_", "search_mode": "content"},
    )
    assert special.status_code == 200
    assert special.get_json()["items"] == []


def test_hybrid_search_uses_validated_model_intent_and_falls_back(client, monkeypatch):
    from app.services import catalog_search

    monkeypatch.setattr(
        catalog_search,
        "_interpret_with_model",
        lambda _query, user=None: CatalogIntent(
            intent_summary="长辈心血管体检",
            audience_terms=["长辈"],
            health_topics=["心血管"],
            location_terms=[],
            package_terms=[],
        ),
    )
    hybrid = client.get(
        "/api/public/organizations",
        query_string={
            "q": "想给家里老人找一个关注循环风险的体检",
            "search_mode": "hybrid",
        },
    )
    assert hybrid.status_code == 200
    assert hybrid.get_json()["search"]["mode"] == "hybrid"
    assert hybrid.get_json()["items"]

    def fail(_query, user=None):
        raise AiProviderError("timeout", code="provider_timeout")

    monkeypatch.setattr(catalog_search, "_interpret_with_model", fail)
    fallback = client.get(
        "/api/public/organizations",
        query_string={"q": "想找一个完全陌生的复杂需求", "search_mode": "hybrid"},
    )
    assert fallback.status_code == 200
    assert fallback.get_json()["search"]["mode"] == "content_fallback"


def test_smart_availability_keeps_date_capacity_and_match_metadata(client):
    headers = _login(client)
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)
    response = client.get(
        "/api/appointments/availability",
        headers=headers,
        query_string={
            "appointment_date": day.isoformat(),
            "q": "女性",
            "search_mode": "content",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["appointment_date"] == day.isoformat()
    assert payload["search"]["suggestions"]
    assert payload["items"]
    assert all("remaining" in item for item in payload["items"])
    assert any(item["matched_packages"] for item in payload["items"])


def test_legacy_catalog_contract_remains_without_search_mode(client):
    headers = _login(client)
    response = client.get("/api/organizations?q=澄心健康", headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert "search" not in payload
    assert payload["items"][0]["name"] == "澄心健康管理中心"
