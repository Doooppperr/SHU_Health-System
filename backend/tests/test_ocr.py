import io

from app.extensions import db
from app.models import HealthIndicator, HealthRecord
from app.services.ocr import HuaweiOCRProvider, OCRMappingService


def _auth_headers(client, username):
    client.post(
        "/api/auth/register",
        json=client.register_payload(username, email=f"{username}@example.com"),
    )
    login_response = client.post(
        "/api/auth/login",
        json=client.login_payload(username),
    )
    access_token = login_response.get_json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


def _first_institution_and_package(client, headers):
    institutions = client.get("/api/institutions", headers=headers).get_json()["items"]
    institution_id = institutions[0]["id"]
    packages = client.get(f"/api/institutions/{institution_id}/packages", headers=headers).get_json()["items"]
    package_id = packages[0]["id"]
    return institution_id, package_id


def test_ocr_mapping_alias():
    service = OCRMappingService()

    assert service.normalize_key(" GLU(空腹) ") == "glu空腹"
    assert service.normalize_key("  空腹血糖  ") == "空腹血糖"


def test_extract_fields_supports_rows_columns_cells():
    provider = HuaweiOCRProvider(
        endpoint="https://example.com",
        ak="dummy-ak",
        sk="dummy-sk",
        project_id="dummy-project",
        api_path="/v2/{project_id}/ocr/general-table",
    )

    response_data = {
        "result": {
            "words_region_list": [
                {
                    "type": "table",
                    "words_block_list": [
                        {"words": "项目代码", "rows": [0], "columns": [0]},
                        {"words": "检查项目", "rows": [0], "columns": [1]},
                        {"words": "结果", "rows": [0], "columns": [2]},
                        {"words": "BMI", "rows": [1], "columns": [0]},
                        {"words": "体重指数", "rows": [1], "columns": [1]},
                        {"words": "24.6", "rows": [1], "columns": [2]},
                        {"words": "GLU", "rows": [2], "columns": [0]},
                        {"words": "空腹血糖", "rows": [2], "columns": [1]},
                        {"words": "7.2", "rows": [2], "columns": [2]},
                        {"words": "TC", "rows": [3], "columns": [0]},
                        {"words": "总胆固醇", "rows": [3], "columns": [1]},
                        {"words": "5.8", "rows": [3], "columns": [2]},
                    ],
                }
            ]
        }
    }

    fields = provider._extract_fields(response_data)

    field_pairs = {(item["label"], item["value"], item.get("source")) for item in fields}
    assert ("BMI", "24.6", "table") in field_pairs
    assert ("GLU", "7.2", "table") in field_pairs
    assert ("TC", "5.8", "table") in field_pairs


def test_extract_fields_supports_adjacent_label_value_lines():
    provider = HuaweiOCRProvider(
        endpoint="https://example.com",
        ak="dummy-ak",
        sk="dummy-sk",
        project_id="dummy-project",
        api_path="/v2/{project_id}/ocr/general-table",
    )

    response_data = {
        "result": {
            "words_region_list": [
                {
                    "type": "text",
                    "words_block_list": [
                        {"words": "检验结果"},
                        {"words": "空腹血糖"},
                        {"words": "6.8 mmol/L"},
                        {"words": "总胆固醇"},
                        {"words": "5.4 mmol/L"},
                        {"words": "甘油三酯"},
                        {"words": "1.9 mmol/L"},
                        {"words": "ALT"},
                        {"words": "41 U/L"},
                    ],
                }
            ]
        }
    }

    fields = provider._extract_fields(response_data)
    field_pairs = {(item["label"], item["value"], item.get("source")) for item in fields}

    assert ("空腹血糖", "6.8 mmol/L", "text") in field_pairs
    assert ("总胆固醇", "5.4 mmol/L", "text") in field_pairs
    assert ("甘油三酯", "1.9 mmol/L", "text") in field_pairs
    assert ("ALT", "41 U/L", "text") in field_pairs


def test_upload_ocr_parse_and_auto_confirm_flow(client, app):
    headers = _auth_headers(client, "ocr_user")
    institution_id, package_id = _first_institution_and_package(client, headers)

    response = client.post(
        "/api/records/upload",
        data={
            "exam_date": "2026-04-08",
            "institution_id": str(institution_id),
            "package_id": str(package_id),
            "file": (io.BytesIO(b"fake pdf"), "report.pdf"),
        },
        headers=headers,
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()

    assert payload["item"]["status"] == "parsed"
    assert payload["ocr"]["mapped_count"] >= 3
    assert payload["ocr"]["unmatched_count"] >= 1
    assert payload["item"]["indicator_count"] == 0
    assert payload["ocr"]["candidate_mappings"]

    record_id = payload["item"]["id"]

    confirm_response = client.put(f"/api/records/{record_id}/confirm", headers=headers)
    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.get_json()
    assert confirm_payload["item"]["status"] == "confirmed"
    assert confirm_payload["ocr"]["confirm_source"] == "auto_high_confidence"
    assert confirm_payload["ocr"]["confirmed_count"] >= 3

    with app.app_context():
        record = db.session.get(HealthRecord, record_id)
        assert record is not None
        assert record.status == "confirmed"
        indicators = HealthIndicator.query.filter_by(record_id=record_id).all()
        assert len(indicators) >= 3


def test_upload_ocr_parse_and_manual_confirm_flow(client, app):
    headers = _auth_headers(client, "ocr_manual_user")
    institution_id, package_id = _first_institution_and_package(client, headers)

    upload_response = client.post(
        "/api/records/upload",
        data={
            "exam_date": "2026-04-08",
            "institution_id": str(institution_id),
            "package_id": str(package_id),
            "file": (io.BytesIO(b"fake pdf"), "report.pdf"),
        },
        headers=headers,
        content_type="multipart/form-data",
    )

    assert upload_response.status_code == 201
    upload_payload = upload_response.get_json()
    record_id = upload_payload["item"]["id"]

    first_candidate = upload_payload["ocr"]["candidate_mappings"][0]
    confirm_response = client.put(
        f"/api/records/{record_id}/confirm",
        headers=headers,
        json={
            "confirmed_mappings": [
                {
                    "indicator_dict_id": first_candidate["indicator_dict_id"],
                    "value": first_candidate["value"],
                    "score": first_candidate["score"],
                }
            ]
        },
    )

    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.get_json()
    assert confirm_payload["ocr"]["confirm_source"] == "manual_selection"
    assert confirm_payload["ocr"]["confirmed_count"] == 1

    with app.app_context():
        indicators = HealthIndicator.query.filter_by(record_id=record_id).all()
        assert len(indicators) == 1


def test_upload_ocr_requires_file(client):
    headers = _auth_headers(client, "ocr_missing_file_user")

    response = client.post(
        "/api/records/upload",
        data={"exam_date": "2026-04-08"},
        headers=headers,
        content_type="multipart/form-data",
    )

    assert response.status_code == 400

