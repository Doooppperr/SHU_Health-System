import io
import json
from datetime import date

from sqlalchemy import update as sqlalchemy_update

from app.records import routes as record_routes
from app.extensions import db
from app.models import HealthIndicator, HealthRecord, IndicatorDict, User
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


def _create_parsed_record(app, username):
    with app.app_context():
        user = User.query.filter_by(username=username).one()
        record = HealthRecord(
            owner_id=user.id,
            uploader_id=user.id,
            exam_date=date(2026, 4, 11),
            status="parsed",
        )
        db.session.add(record)
        db.session.commit()
        return record.id


def _set_auto_candidates(app, record_id, candidates):
    with app.app_context():
        record = db.session.get(HealthRecord, record_id)
        record.ocr_raw_text = json.dumps(
            {"mapping": {"candidate_mappings": candidates}}
        )
        db.session.commit()


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


def test_upload_ocr_can_attach_to_an_existing_manual_record(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    headers = _auth_headers(client, "ocr_attach_user")
    create_response = client.post(
        "/api/records",
        json={"exam_date": "2026-04-09", "status": "confirmed"},
        headers=headers,
    )
    assert create_response.status_code == 201
    record_id = create_response.get_json()["item"]["id"]

    with app.app_context():
        alt_id = IndicatorDict.query.filter_by(code="ALT").one().id
        crea_id = IndicatorDict.query.filter_by(code="CREA").one().id

    for indicator_dict_id, value in [(alt_id, "32"), (crea_id, "88")]:
        indicator_response = client.post(
            f"/api/records/{record_id}/indicators",
            json={"indicator_dict_id": indicator_dict_id, "value": value},
            headers=headers,
        )
        assert indicator_response.status_code == 201

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    previous_report = reports_dir / "previous.pdf"
    previous_report.write_bytes(b"previous report")
    with app.app_context():
        record = db.session.get(HealthRecord, record_id)
        record.report_file_url = "/uploads/reports/previous.pdf"
        record.ocr_raw_text = json.dumps({"engine": "previous"})
        db.session.commit()

    with app.app_context():
        record_count_before = HealthRecord.query.count()

    response = client.post(
        "/api/records/upload",
        data={
            "record_id": str(record_id),
            "file": (io.BytesIO(b"replacement report"), "report.pdf"),
        },
        headers=headers,
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["item"]["id"] == record_id
    assert payload["item"]["exam_date"] == "2026-04-09"
    assert payload["item"]["status"] == "confirmed"
    assert payload["item"]["indicator_count"] == 2
    assert {item["value"] for item in payload["item"]["indicators"]} == {"32", "88"}
    assert payload["ocr"]["candidate_mappings"]
    assert payload["ocr"]["pending_confirmation"] is True
    attachment_id = payload["ocr"]["attachment_id"]
    assert attachment_id

    with app.app_context():
        assert HealthRecord.query.count() == record_count_before
        record = db.session.get(HealthRecord, record_id)
        assert record.status == "confirmed"
        assert record.report_file_url == "/uploads/reports/previous.pdf"
        pending = json.loads(record.ocr_raw_text)["_pending_attachment"]
        pending_report = tmp_path / pending["report_file_url"].removeprefix("/uploads/")
        assert pending_report.is_file()
        assert previous_report.is_file()

    confirm_response = client.put(
        f"/api/records/{record_id}/confirm",
        json={"attachment_id": attachment_id},
        headers=headers,
    )
    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.get_json()
    assert confirm_payload["item"]["id"] == record_id
    assert confirm_payload["item"]["status"] == "confirmed"

    with app.app_context():
        assert HealthRecord.query.count() == record_count_before
        stored_alt = HealthIndicator.query.filter_by(
            record_id=record_id,
            indicator_dict_id=alt_id,
        ).one()
        assert stored_alt.value == "46"
        assert stored_alt.source == "ocr"
        stored_crea = HealthIndicator.query.filter_by(
            record_id=record_id,
            indicator_dict_id=crea_id,
        ).one()
        assert stored_crea.value == "88"
        assert stored_crea.source == "manual"
        record = db.session.get(HealthRecord, record_id)
        assert record.report_file_url == pending["report_file_url"]
        assert "_pending_attachment" not in json.loads(record.ocr_raw_text)

    assert pending_report.is_file()
    assert not previous_report.exists()


def test_failed_attach_confirmation_keeps_the_original_record_and_report(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    headers = _auth_headers(client, "ocr_attach_rollback")
    record_id = client.post(
        "/api/records",
        json={"exam_date": "2026-04-09", "status": "confirmed"},
        headers=headers,
    ).get_json()["item"]["id"]

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    previous_report = reports_dir / "original.pdf"
    previous_report.write_bytes(b"original report")
    with app.app_context():
        record = db.session.get(HealthRecord, record_id)
        record.report_file_url = "/uploads/reports/original.pdf"
        db.session.commit()

    upload_response = client.post(
        "/api/records/upload",
        data={
            "record_id": str(record_id),
            "file": (io.BytesIO(b"pending report"), "pending.pdf"),
        },
        headers=headers,
        content_type="multipart/form-data",
    )
    assert upload_response.status_code == 200
    attachment_id = upload_response.get_json()["ocr"]["attachment_id"]

    confirm_response = client.put(
        f"/api/records/{record_id}/confirm",
        json={
            "attachment_id": attachment_id,
            "confirmed_mappings": [{"indicator_dict_id": 999999, "value": "1"}],
        },
        headers=headers,
    )
    assert confirm_response.status_code == 400

    with app.app_context():
        record = db.session.get(HealthRecord, record_id)
        assert record.status == "confirmed"
        assert record.report_file_url == "/uploads/reports/original.pdf"
        pending = json.loads(record.ocr_raw_text)["_pending_attachment"]
        pending_report = tmp_path / pending["report_file_url"].removeprefix("/uploads/")
        assert pending_report.is_file()
    assert previous_report.is_file()


def test_pending_attachment_version_prevents_stale_confirm_and_can_be_cancelled(
    client, app, tmp_path
):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    headers = _auth_headers(client, "ocr_attach_versioned")
    record_id = client.post(
        "/api/records",
        json={"exam_date": "2026-04-09", "status": "confirmed"},
        headers=headers,
    ).get_json()["item"]["id"]

    first_upload = client.post(
        "/api/records/upload",
        data={
            "record_id": str(record_id),
            "file": (io.BytesIO(b"first pending report"), "first.pdf"),
        },
        headers=headers,
        content_type="multipart/form-data",
    )
    first_attachment_id = first_upload.get_json()["ocr"]["attachment_id"]

    second_upload = client.post(
        "/api/records/upload",
        data={
            "record_id": str(record_id),
            "file": (io.BytesIO(b"second pending report"), "second.pdf"),
        },
        headers=headers,
        content_type="multipart/form-data",
    )
    second_payload = second_upload.get_json()
    second_attachment_id = second_payload["ocr"]["attachment_id"]
    assert second_attachment_id != first_attachment_id

    stale_confirm = client.put(
        f"/api/records/{record_id}/confirm",
        json={"attachment_id": first_attachment_id},
        headers=headers,
    )
    assert stale_confirm.status_code == 409
    assert stale_confirm.get_json()["code"] == "OCR_ATTACHMENT_STALE"

    pending_response = client.get(
        f"/api/records/{record_id}/ocr-pending",
        headers=headers,
    )
    assert pending_response.status_code == 200
    pending_payload = pending_response.get_json()
    assert pending_payload["ocr"]["attachment_id"] == second_attachment_id
    assert pending_payload["item"]["ocr_pending_confirmation"] is True

    stale_cancel = client.delete(
        f"/api/records/{record_id}/ocr-pending",
        json={"attachment_id": first_attachment_id},
        headers=headers,
    )
    assert stale_cancel.status_code == 409

    with app.app_context():
        record = db.session.get(HealthRecord, record_id)
        pending = json.loads(record.ocr_raw_text)["_pending_attachment"]
        pending_file = tmp_path / pending["report_file_url"].removeprefix("/uploads/")
        assert pending_file.is_file()

    cancel_response = client.delete(
        f"/api/records/{record_id}/ocr-pending",
        json={"attachment_id": second_attachment_id},
        headers=headers,
    )
    assert cancel_response.status_code == 200
    assert cancel_response.get_json()["item"]["ocr_pending_confirmation"] is False
    assert not pending_file.exists()


def test_delete_record_cleans_official_and_pending_ocr_reports(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    headers = _auth_headers(client, "ocr_delete_pending")
    record_id = client.post(
        "/api/records",
        json={"exam_date": "2026-04-09", "status": "confirmed"},
        headers=headers,
    ).get_json()["item"]["id"]

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    official_report = reports_dir / "official.pdf"
    official_report.write_bytes(b"official report")
    with app.app_context():
        record = db.session.get(HealthRecord, record_id)
        record.report_file_url = "/uploads/reports/official.pdf"
        record.ocr_raw_text = json.dumps({"engine": "previous"})
        indicator_dict_id = IndicatorDict.query.filter_by(code="ALT").one().id
        db.session.add(
            HealthIndicator(
                record_id=record_id,
                indicator_dict_id=indicator_dict_id,
                value="32",
                source="manual",
            )
        )
        db.session.commit()

    upload_response = client.post(
        "/api/records/upload",
        data={
            "record_id": str(record_id),
            "file": (io.BytesIO(b"pending report"), "pending.pdf"),
        },
        headers=headers,
        content_type="multipart/form-data",
    )
    assert upload_response.status_code == 200

    with app.app_context():
        record = db.session.get(HealthRecord, record_id)
        pending = json.loads(record.ocr_raw_text)["_pending_attachment"]
        pending_report = tmp_path / pending["report_file_url"].removeprefix("/uploads/")
        assert official_report.is_file()
        assert pending_report.is_file()

    delete_response = client.delete(f"/api/records/{record_id}", headers=headers)

    assert delete_response.status_code == 200
    assert not official_report.exists()
    assert not pending_report.exists()
    with app.app_context():
        assert db.session.get(HealthRecord, record_id) is None
        assert HealthIndicator.query.filter_by(record_id=record_id).count() == 0


def test_delete_record_snapshot_conflict_keeps_record_and_report(
    client, app, tmp_path, monkeypatch
):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    headers = _auth_headers(client, "ocr_delete_conflict")
    record_id = client.post(
        "/api/records",
        json={"exam_date": "2026-04-09", "status": "confirmed"},
        headers=headers,
    ).get_json()["item"]["id"]

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    official_report = reports_dir / "official.pdf"
    official_report.write_bytes(b"official report")
    with app.app_context():
        record = db.session.get(HealthRecord, record_id)
        record.report_file_url = "/uploads/reports/official.pdf"
        record.ocr_raw_text = json.dumps({"engine": "previous"})
        db.session.commit()

    def no_matching_record_update(model):
        return sqlalchemy_update(model).where(HealthRecord.id == -1)

    monkeypatch.setattr(record_routes, "update", no_matching_record_update)

    delete_response = client.delete(f"/api/records/{record_id}", headers=headers)

    assert delete_response.status_code == 409
    assert delete_response.get_json()["code"] == "RECORD_DELETE_CONFLICT"
    assert official_report.is_file()
    with app.app_context():
        record = db.session.get(HealthRecord, record_id)
        assert record is not None
        assert record.report_file_url == "/uploads/reports/official.pdf"


def test_upload_ocr_cannot_attach_to_an_inaccessible_record(client):
    owner_headers = _auth_headers(client, "ocr_attach_owner")
    record_id = client.post(
        "/api/records",
        json={"exam_date": "2026-04-09"},
        headers=owner_headers,
    ).get_json()["item"]["id"]
    other_headers = _auth_headers(client, "ocr_attach_other")

    response = client.post(
        "/api/records/upload",
        data={
            "record_id": str(record_id),
            "file": (io.BytesIO(b"private report"), "report.pdf"),
        },
        headers=other_headers,
        content_type="multipart/form-data",
    )

    assert response.status_code == 404
    assert response.get_json() == {"message": "record not found"}


def test_upload_ocr_rejects_an_invalid_target_record_id(client):
    headers = _auth_headers(client, "ocr_attach_invalid_id")

    response = client.post(
        "/api/records/upload",
        data={
            "record_id": "health3",
            "file": (io.BytesIO(b"report"), "report.pdf"),
        },
        headers=headers,
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json() == {"message": "record_id must be a positive integer"}


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


def test_manual_confirm_accepts_ocr_value_with_explicit_reference_suffix(client, app):
    username = "ocr_reference_suffix_user"
    headers = _auth_headers(client, username)
    record_id = _create_parsed_record(app, username)
    raw_values = {
        "FBG": "5.6 mmol/L(reference 3.9-6.1)",
        "TC": "4.9 mmol/L(reference <=5.2)",
        "TG": "1.4 mmol/L(reference<=1.7)",
        "HDL": "1.15 mmol/L (reference 1.0-2.3)",
        "LDL": "3.2 mmol/L(reference <=3.4)",
        "ALT": "32 U/L",
        "UA": "365 μmol/L(reference 155 - 428)",
        "CREA": "88 μmol/L",
    }
    with app.app_context():
        dictionaries = {
            item.code: item.id
            for item in IndicatorDict.query.filter(
                IndicatorDict.code.in_(raw_values)
            ).all()
        }

    response = client.put(
        f"/api/records/{record_id}/confirm",
        headers=headers,
        json={
            "confirmed_mappings": [
                {"indicator_dict_id": dictionaries[code], "value": value}
                for code, value in raw_values.items()
            ]
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ocr"]["confirmed_count"] == 8
    assert payload["item"]["status"] == "confirmed"
    with app.app_context():
        stored = {
            item.indicator_dict.code: item.value
            for item in HealthIndicator.query.filter_by(record_id=record_id).all()
        }
    assert stored == {
        "FBG": "5.6",
        "TC": "4.9",
        "TG": "1.4",
        "HDL": "1.15",
        "LDL": "3.2",
        "ALT": "32",
        "UA": "365",
        "CREA": "88",
    }


def test_manual_confirm_reports_invalid_ocr_value_separately_from_dictionary_id(
    client, app
):
    username = "ocr_invalid_value_user"
    headers = _auth_headers(client, username)
    record_id = _create_parsed_record(app, username)
    with app.app_context():
        fbg_id = IndicatorDict.query.filter_by(code="FBG").one().id

    response = client.put(
        f"/api/records/{record_id}/confirm",
        headers=headers,
        json={
            "confirmed_mappings": [
                {"indicator_dict_id": fbg_id, "value": "5.6 / 6.1"}
            ]
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["message"] == "some indicator values are invalid"
    assert payload["invalid_indicator_values"][0]["indicator_dict_id"] == fbg_id
    assert "invalid_indicator_dict_ids" not in payload
    with app.app_context():
        assert db.session.get(HealthRecord, record_id).status == "parsed"
        assert HealthIndicator.query.filter_by(record_id=record_id).count() == 0


def test_auto_confirm_rolls_back_when_indicator_dictionary_id_is_invalid(client, app):
    username = "ocr_auto_invalid_id_user"
    headers = _auth_headers(client, username)
    record_id = _create_parsed_record(app, username)
    with app.app_context():
        alt_id = IndicatorDict.query.filter_by(code="ALT").one().id
        invalid_id = (db.session.query(db.func.max(IndicatorDict.id)).scalar() or 0) + 1000
    _set_auto_candidates(
        app,
        record_id,
        [
            {"indicator_dict_id": alt_id, "value": "32 U/L", "score": 0.99},
            {"indicator_dict_id": invalid_id, "value": "5.6", "score": 0.99},
        ],
    )

    response = client.put(f"/api/records/{record_id}/confirm", headers=headers)

    assert response.status_code == 400
    payload = response.get_json()
    assert payload == {
        "message": "some indicator_dict_id are invalid",
        "invalid_indicator_dict_ids": [invalid_id],
    }
    with app.app_context():
        assert db.session.get(HealthRecord, record_id).status == "parsed"
        assert HealthIndicator.query.filter_by(record_id=record_id).count() == 0


def test_auto_confirm_rolls_back_when_indicator_value_is_invalid(client, app):
    username = "ocr_auto_invalid_value_user"
    headers = _auth_headers(client, username)
    record_id = _create_parsed_record(app, username)
    with app.app_context():
        dictionaries = {
            item.code: item.id
            for item in IndicatorDict.query.filter(
                IndicatorDict.code.in_(["ALT", "FBG"])
            ).all()
        }
    _set_auto_candidates(
        app,
        record_id,
        [
            {
                "indicator_dict_id": dictionaries["ALT"],
                "value": "32 U/L",
                "score": 0.99,
            },
            {
                "indicator_dict_id": dictionaries["FBG"],
                "value": "5.6 / 6.1",
                "score": 0.99,
            },
        ],
    )

    response = client.put(f"/api/records/{record_id}/confirm", headers=headers)

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["message"] == "some indicator values are invalid"
    assert payload["invalid_indicator_values"] == [
        {
            "indicator_dict_id": dictionaries["FBG"],
            "message": "numeric indicator value must contain one number",
        }
    ]
    with app.app_context():
        assert db.session.get(HealthRecord, record_id).status == "parsed"
        assert HealthIndicator.query.filter_by(record_id=record_id).count() == 0

