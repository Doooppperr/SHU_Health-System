from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
import zlib

import pytest
from PIL import Image

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import HealthRecord, Institution, InstitutionImage, InstitutionInvite, User


def _register(client, username, *, invite_code=None):
    payload = client.register_payload(username, email=f"{username}@example.com")
    if invite_code:
        payload["invite_code"] = invite_code
    response = client.post("/api/auth/register", json=payload)
    return response


def _standalone_register_payload(client, username, *, invite_code=None):
    captcha = client.get("/api/auth/captcha").get_json()
    payload = {
        "username": username,
        "password": "secret123",
        "email": f"{username}@example.com",
        "captcha_id": captcha["captcha_id"],
        "captcha_answer": captcha["captcha_answer"],
    }
    if invite_code:
        payload["invite_code"] = invite_code
    return payload


def _headers_from_response(response):
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def _make_admin(client, app, username="workspace_admin"):
    response = _register(client, username)
    assert response.status_code == 201
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        user.role = "admin"
        db.session.commit()
    return _headers_from_response(response)


def _first_institution(client, headers):
    response = client.get("/api/admin/institutions", headers=headers)
    assert response.status_code == 200
    return response.get_json()["items"][0]


def _issue_org_admin(client, app, institution_id, suffix):
    admin_headers = _make_admin(client, app, f"invite_admin_{suffix}")
    invite_response = client.post(
        f"/api/admin/institutions/{institution_id}/invite",
        headers=admin_headers,
    )
    assert invite_response.status_code == 201
    invite_code = invite_response.get_json()["invite_code"]
    org_response = _register(client, f"org_manager_{suffix}", invite_code=invite_code)
    assert org_response.status_code == 201
    return admin_headers, _headers_from_response(org_response), invite_code, org_response.get_json()["user"]


def _image_upload_payload(image_format="PNG", color=(25, 120, 180)):
    buffer = BytesIO()
    Image.new("RGB", (32, 24), color).save(buffer, format=image_format)
    buffer.seek(0)
    extension = "jpg" if image_format == "JPEG" else image_format.lower()
    return buffer, f"test.{extension}"


def _oversized_png_header_payload():
    buffer, _filename = _image_upload_payload("PNG")
    raw = bytearray(buffer.getvalue())
    raw[16:20] = (6001).to_bytes(4, "big")
    raw[29:33] = zlib.crc32(raw[12:29]).to_bytes(4, "big")
    return BytesIO(raw), "oversized.png"


def test_invitation_registration_role_isolation_and_revoke(client, app):
    admin_headers = _make_admin(client, app, "role_admin")
    institution = _first_institution(client, admin_headers)

    issue_response = client.post(
        f"/api/admin/institutions/{institution['id']}/invite",
        headers=admin_headers,
    )
    assert issue_response.status_code == 201
    issue_payload = issue_response.get_json()
    assert issue_payload["invite_code"]
    assert "code_hash" not in issue_payload["item"]

    org_response = _register(client, "role_org", invite_code=issue_payload["invite_code"])
    assert org_response.status_code == 201
    org_user = org_response.get_json()["user"]
    org_headers = _headers_from_response(org_response)
    assert org_user["role"] == "institution_admin"
    assert org_user["managed_institution_id"] == institution["id"]

    reused = _register(client, "role_org_second", invite_code=issue_payload["invite_code"])
    assert reused.status_code in {400, 409}
    assert client.get("/api/org/dashboard", headers=org_headers).status_code == 200
    assert client.get("/api/records", headers=org_headers).status_code == 403
    assert client.get("/api/friends", headers=org_headers).status_code == 403
    assert client.get("/api/trends/indicators/1", headers=org_headers).status_code == 403
    assert client.post(
        "/api/ai/chat",
        headers=org_headers,
        json={"message": "分析档案", "selected_record_ids": [1]},
    ).status_code == 403
    assert client.get("/api/admin/dashboard", headers=org_headers).status_code == 403

    revoke = client.post(
        f"/api/admin/users/{org_user['id']}/revoke-institution-admin",
        headers=admin_headers,
    )
    assert revoke.status_code == 200
    assert revoke.get_json()["item"]["role"] == "user"
    assert client.get("/api/org/dashboard", headers=org_headers).status_code == 403
    assert client.get("/api/records/summary", headers=org_headers).status_code == 200


def test_revoked_invitation_stays_invalid_after_reissue(client, app):
    admin_headers = _make_admin(client, app, "invite_rotation_admin")
    institution = _first_institution(client, admin_headers)
    first_issue = client.post(
        f"/api/admin/institutions/{institution['id']}/invite",
        headers=admin_headers,
    )
    first_code = first_issue.get_json()["invite_code"]
    assert client.delete(
        f"/api/admin/institutions/{institution['id']}/invite",
        headers=admin_headers,
    ).status_code == 200
    assert _register(client, "revoked_invite_user", invite_code=first_code).status_code == 400

    second_issue = client.post(
        f"/api/admin/institutions/{institution['id']}/invite",
        headers=admin_headers,
    )
    second_code = second_issue.get_json()["invite_code"]
    assert second_code != first_code
    assert _register(client, "rotated_invite_user", invite_code=second_code).status_code == 201
    assert _register(client, "rotated_invite_reuse", invite_code=second_code).status_code in {400, 409}


def test_invitation_is_consumed_atomically_under_concurrent_registration(tmp_path):
    original_uri = TestingConfig.SQLALCHEMY_DATABASE_URI
    database_path = (tmp_path / "concurrent-invite.db").resolve()
    TestingConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path.as_posix()}"
    concurrent_app = None
    try:
        concurrent_app = create_app("testing")
        bootstrap_client = concurrent_app.test_client()
        admin_payload = _standalone_register_payload(
            bootstrap_client,
            "concurrent_invite_admin",
        )
        admin_response = bootstrap_client.post("/api/auth/register", json=admin_payload)
        assert admin_response.status_code == 201
        admin_headers = _headers_from_response(admin_response)
        with concurrent_app.app_context():
            admin = User.query.filter_by(username="concurrent_invite_admin").first()
            admin.role = "admin"
            institution_id = Institution.query.order_by(Institution.id.asc()).first().id
            db.session.commit()

        issue = bootstrap_client.post(
            f"/api/admin/institutions/{institution_id}/invite",
            headers=admin_headers,
        )
        assert issue.status_code == 201
        invite_code = issue.get_json()["invite_code"]

        first_client = concurrent_app.test_client()
        second_client = concurrent_app.test_client()
        payloads = [
            _standalone_register_payload(
                first_client,
                "concurrent_org_first",
                invite_code=invite_code,
            ),
            _standalone_register_payload(
                second_client,
                "concurrent_org_second",
                invite_code=invite_code,
            ),
        ]
        registration_barrier = Barrier(2)

        def register_at_once(payload):
            with concurrent_app.test_client() as request_client:
                registration_barrier.wait(timeout=5)
                response = request_client.post("/api/auth/register", json=payload)
                return response.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(register_at_once, payloads))

        assert statuses.count(201) == 1
        assert all(status in {201, 400, 409} for status in statuses)
        with concurrent_app.app_context():
            assert User.query.filter_by(
                role="institution_admin",
                managed_institution_id=institution_id,
            ).count() == 1
            invite = InstitutionInvite.query.filter_by(
                institution_id=institution_id
            ).one()
            assert invite.status == "used"
            assert invite.used_by_user_id is not None
    finally:
        TestingConfig.SQLALCHEMY_DATABASE_URI = original_uri
        if concurrent_app is not None:
            with concurrent_app.app_context():
                db.session.remove()
                db.engine.dispose()


def test_optional_source_indicator_normalization_and_clear_source(client, app):
    user_response = _register(client, "normalized_user")
    user_headers = _headers_from_response(user_response)

    create = client.post(
        "/api/records",
        headers=user_headers,
        json={"exam_date": "2026-07-01"},
    )
    assert create.status_code == 201
    item = create.get_json()["item"]
    assert item["institution_id"] is None
    assert item["package_id"] is None

    fbg = next(
        row
        for row in client.get("/api/indicators/dicts", headers=user_headers).get_json()["items"]
        if row["code"] == "FBG"
    )
    add = client.post(
        f"/api/records/{item['id']}/indicators",
        headers=user_headers,
        json={"indicator_dict_id": fbg["id"], "value": "7.20 mmol/L ↑"},
    )
    assert add.status_code == 201
    indicator = add.get_json()["item"]
    assert indicator["value"] == "7.2"
    assert indicator["is_abnormal"] is True

    wrong_unit = client.put(
        f"/api/records/{item['id']}/indicators/{indicator['id']}",
        headers=user_headers,
        json={"value": "126 mg/dL"},
    )
    assert wrong_unit.status_code == 400

    admin_headers = _make_admin(client, app, "source_admin")
    institution = _first_institution(client, admin_headers)
    package = client.get(
        f"/api/admin/institutions/{institution['id']}/packages",
        headers=admin_headers,
    ).get_json()["items"][0]
    attach = client.put(
        f"/api/records/{item['id']}",
        headers=user_headers,
        json={"institution_id": institution["id"], "package_id": package["id"]},
    )
    assert attach.status_code == 200
    clear = client.put(
        f"/api/records/{item['id']}",
        headers=user_headers,
        json={"institution_id": None},
    )
    assert clear.status_code == 200
    assert clear.get_json()["item"]["institution_id"] is None
    assert clear.get_json()["item"]["package_id"] is None


def test_institution_health_is_confirmed_scoped_sanitized_and_read_only(client, app):
    bootstrap_admin = _make_admin(client, app, "health_bootstrap_admin")
    institution = _first_institution(client, bootstrap_admin)
    _admin_headers, org_headers, _code, _org_user = _issue_org_admin(
        client, app, institution["id"], "health"
    )
    user_response = _register(client, "health_record_owner")
    user_headers = _headers_from_response(user_response)

    confirmed = client.post(
        "/api/records",
        headers=user_headers,
        json={
            "exam_date": "2026-05-01",
            "institution_id": institution["id"],
            "status": "confirmed",
        },
    )
    assert confirmed.status_code == 201
    record_id = confirmed.get_json()["item"]["id"]
    indicator_dicts = client.get(
        "/api/indicators/dicts", headers=user_headers
    ).get_json()["items"]
    fbg = next(item for item in indicator_dicts if item["code"] == "FBG")
    add_indicator = client.post(
        f"/api/records/{record_id}/indicators",
        headers=user_headers,
        json={"indicator_dict_id": fbg["id"], "value": "5.6"},
    )
    assert add_indicator.status_code == 201
    with app.app_context():
        db.session.get(HealthRecord, record_id).report_file_url = "/uploads/reports/private.pdf"
        db.session.commit()
    draft = client.post(
        "/api/records",
        headers=user_headers,
        json={
            "exam_date": "2026-06-01",
            "institution_id": institution["id"],
            "status": "draft",
        },
    )
    assert draft.status_code == 201
    no_source = client.post(
        "/api/records",
        headers=user_headers,
        json={"exam_date": "2026-06-02", "status": "confirmed"},
    )
    assert no_source.status_code == 201

    listing = client.get("/api/institution-health/records", headers=org_headers)
    assert listing.status_code == 200
    assert [row["id"] for row in listing.get_json()["items"]] == [record_id]
    assert listing.get_json()["items"][0]["display_id"] == f"health{record_id}"
    assert "report_file_url" not in listing.get_data(as_text=True)
    assert "uploader" not in listing.get_data(as_text=True)
    assert "email" not in listing.get_data(as_text=True)
    assert "phone" not in listing.get_data(as_text=True)

    detail = client.get(f"/api/institution-health/records/{record_id}", headers=org_headers)
    assert detail.status_code == 200
    assert detail.get_json()["item"]["display_id"] == f"health{record_id}"
    detail_text = detail.get_data(as_text=True)
    assert "report_file_url" not in detail_text
    assert "private.pdf" not in detail_text
    assert client.put(
        f"/api/institution-health/records/{record_id}",
        headers=org_headers,
        json={"status": "draft"},
    ).status_code == 405
    assert client.get("/uploads/reports/private.pdf").status_code == 404

    dashboard = client.get("/api/org/dashboard", headers=org_headers)
    assert dashboard.status_code == 200
    recent_record = next(
        item
        for item in dashboard.get_json()["recent_records"]
        if item["id"] == record_id
    )
    assert recent_record["display_id"] == f"health{record_id}"

    trends = client.get(
        "/api/institution-health/trends",
        headers=org_headers,
        query_string={"indicator_dict_id": fbg["id"]},
    )
    assert trends.status_code == 200
    assert trends.get_json()["series"][0]["record_id"] == record_id
    assert trends.get_json()["series"][0]["record_display_id"] == f"health{record_id}"

    clear_source = client.put(
        f"/api/records/{record_id}",
        headers=user_headers,
        json={"institution_id": None},
    )
    assert clear_source.status_code == 200
    assert client.get(
        f"/api/institution-health/records/{record_id}", headers=org_headers
    ).status_code == 404


def test_org_gallery_reorders_cover_and_strips_private_storage_key(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    admin_headers = _make_admin(client, app, "gallery_admin")
    institution = _first_institution(client, admin_headers)
    _unused, org_headers, _code, _org_user = _issue_org_admin(
        client, app, institution["id"], "gallery"
    )

    first = client.post(
        "/api/org/images",
        headers=org_headers,
        data={"file": _image_upload_payload("JPEG", (200, 20, 20))},
        content_type="multipart/form-data",
    )
    second = client.post(
        "/api/org/images",
        headers=org_headers,
        data={"file": _image_upload_payload("PNG", (20, 200, 20))},
        content_type="multipart/form-data",
    )
    assert first.status_code == 201
    assert second.status_code == 201
    first_item = first.get_json()["item"]
    second_item = second.get_json()["item"]
    assert "storage_key" not in first_item
    assert first_item["is_cover"] is True
    assert client.get(first_item["image_url"]).status_code == 200

    reorder = client.put(
        "/api/org/images/order",
        headers=org_headers,
        json={"image_ids": [second_item["id"], first_item["id"]]},
    )
    assert reorder.status_code == 200
    reordered = reorder.get_json()["items"]
    assert [row["id"] for row in reordered] == [second_item["id"], first_item["id"]]
    assert reordered[0]["is_cover"] is True
    assert reordered[1]["is_cover"] is False

    delete = client.delete(f"/api/org/images/{second_item['id']}", headers=org_headers)
    assert delete.status_code == 200
    assert client.get(second_item["image_url"]).status_code == 404
    assert not (tmp_path / second_item["image_url"].removeprefix("/uploads/")).exists()
    remaining = client.get("/api/org/images", headers=org_headers).get_json()["items"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == first_item["id"]
    assert remaining[0]["is_cover"] is True


def test_org_gallery_enforces_eight_image_limit(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    admin_headers = _make_admin(client, app, "gallery_limit_admin")
    institution = _first_institution(client, admin_headers)
    _unused, org_headers, _code, _org_user = _issue_org_admin(
        client,
        app,
        institution["id"],
        "gallery_limit",
    )

    for index in range(8):
        response = client.post(
            "/api/org/images",
            headers=org_headers,
            data={"file": _image_upload_payload("PNG", (index * 20, 100, 180))},
            content_type="multipart/form-data",
        )
        assert response.status_code == 201

    ninth = client.post(
        "/api/org/images",
        headers=org_headers,
        data={"file": _image_upload_payload("PNG", (220, 100, 180))},
        content_type="multipart/form-data",
    )
    assert ninth.status_code == 400
    assert "at most 8" in ninth.get_json()["message"]


def test_org_gallery_rejects_oversized_dimensions_before_decode(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    admin_headers = _make_admin(client, app, "gallery_dimensions_admin")
    institution = _first_institution(client, admin_headers)
    _unused, org_headers, _code, _org_user = _issue_org_admin(
        client,
        app,
        institution["id"],
        "gallery_dimensions",
    )

    response = client.post(
        "/api/org/images",
        headers=org_headers,
        data={"file": _oversized_png_header_payload()},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["message"] == "image dimensions are too large"


def test_public_upload_route_serves_only_database_whitelisted_institution_images(
    client,
    app,
    tmp_path,
):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    legacy_dir = tmp_path / "logos"
    legacy_dir.mkdir()
    (legacy_dir / "legacy-cover.png").write_bytes(b"legacy-image")
    (legacy_dir / "orphan.png").write_bytes(b"orphan-image")

    with app.app_context():
        institution = Institution.query.order_by(Institution.id.asc()).first()
        image = InstitutionImage(
            institution_id=institution.id,
            storage_key="logos/legacy-cover.png",
            image_url="/uploads/logos/legacy-cover.png",
            sort_order=0,
        )
        db.session.add(image)
        db.session.commit()

    allowed = client.get("/uploads/logos/legacy-cover.png")
    assert allowed.status_code == 200
    assert allowed.data == b"legacy-image"
    assert client.get("/uploads/logos/orphan.png").status_code == 404
    assert client.get("/uploads/reports/private.pdf").status_code == 404


def test_ocr_upload_cleans_saved_report_when_database_commit_fails(
    client,
    app,
    tmp_path,
    monkeypatch,
):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    user_headers = _headers_from_response(_register(client, "upload_cleanup_user"))

    def fail_commit():
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(db.session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        client.post(
            "/api/records/upload",
            headers=user_headers,
            data={
                "file": _image_upload_payload("PNG"),
                "exam_date": "2026-07-09",
            },
            content_type="multipart/form-data",
        )
    report_dir = tmp_path / "reports"
    assert not report_dir.exists() or list(report_dir.iterdir()) == []


def test_admin_soft_deactivates_institution_and_revokes_manager(client, app):
    admin_headers = _make_admin(client, app, "deactivate_admin")
    institution = _first_institution(client, admin_headers)
    _unused, org_headers, _code, org_user = _issue_org_admin(
        client, app, institution["id"], "deactivate"
    )

    response = client.post(
        f"/api/admin/institutions/{institution['id']}/deactivate",
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.get_json()["item"]["is_active"] is False
    assert client.get("/api/org/dashboard", headers=org_headers).status_code == 403
    me = client.get("/api/users/me", headers=org_headers).get_json()["user"]
    assert me["id"] == org_user["id"]
    assert me["role"] == "user"
    assert me["managed_institution_id"] is None

    visible_ids = {
        row["id"]
        for row in client.get("/api/institutions", headers=org_headers).get_json()["items"]
    }
    assert institution["id"] not in visible_ids
    restore = client.post(
        f"/api/admin/institutions/{institution['id']}/restore",
        headers=admin_headers,
    )
    assert restore.status_code == 200
    assert restore.get_json()["item"]["is_active"] is True


def test_inactive_source_stays_historical_but_cannot_be_newly_attached(client, app):
    admin_headers = _make_admin(client, app, "inactive_source_admin")
    institution = _first_institution(client, admin_headers)
    package = client.get(
        f"/api/admin/institutions/{institution['id']}/packages",
        headers=admin_headers,
    ).get_json()["items"][0]
    user_headers = _headers_from_response(_register(client, "inactive_source_user"))
    record = client.post(
        "/api/records",
        headers=user_headers,
        json={
            "exam_date": "2026-04-01",
            "institution_id": institution["id"],
            "package_id": package["id"],
        },
    ).get_json()["item"]

    assert client.post(
        f"/api/admin/institutions/{institution['id']}/deactivate",
        headers=admin_headers,
    ).status_code == 200

    historical_edit = client.put(
        f"/api/records/{record['id']}",
        headers=user_headers,
        json={"exam_date": "2026-04-02"},
    )
    assert historical_edit.status_code == 200
    assert historical_edit.get_json()["item"]["institution_id"] == institution["id"]

    new_attach = client.post(
        "/api/records",
        headers=user_headers,
        json={"exam_date": "2026-04-03", "package_id": package["id"]},
    )
    assert new_attach.status_code == 400
    assert new_attach.get_json()["message"] == "package is inactive" or new_attach.get_json()["message"] == "institution is inactive"


def test_institution_admin_cannot_use_regular_comment_workflow(client, app):
    admin_headers = _make_admin(client, app, "comment_role_admin")
    institution = _first_institution(client, admin_headers)
    _unused, org_headers, _code, _org_user = _issue_org_admin(
        client, app, institution["id"], "comment"
    )

    assert client.get("/api/comments", headers=org_headers).status_code == 403
    assert client.get("/api/comments/mine", headers=org_headers).status_code == 403
    assert client.post(
        "/api/comments",
        headers=org_headers,
        json={"institution_id": institution["id"], "content": "不应提交", "rating": 5},
    ).status_code == 403
    assert client.get("/api/comments/moderation", headers=admin_headers).status_code == 200


def test_report_file_requires_record_authorization(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "authorized.pdf").write_bytes(b"private-report-content")

    owner = _register(client, "report_owner")
    owner_headers = _headers_from_response(owner)
    outsider_headers = _headers_from_response(_register(client, "report_outsider"))
    forged = client.post(
        "/api/records",
        headers=owner_headers,
        json={
            "exam_date": "2026-07-08",
            "report_file_url": "/uploads/reports/authorized.pdf",
        },
    )
    assert forged.status_code == 400

    record = client.post(
        "/api/records",
        headers=owner_headers,
        json={"exam_date": "2026-07-08"},
    ).get_json()["item"]
    with app.app_context():
        db.session.get(HealthRecord, record["id"]).report_file_url = (
            "/uploads/reports/authorized.pdf"
        )
        db.session.commit()

    allowed = client.get(f"/api/records/{record['id']}/file", headers=owner_headers)
    assert allowed.status_code == 200
    assert allowed.data == b"private-report-content"
    allowed.close()
    assert client.get(
        f"/api/records/{record['id']}/file", headers=outsider_headers
    ).status_code == 404
    assert client.get("/uploads/reports/authorized.pdf").status_code == 404
    assert client.delete(
        f"/api/records/{record['id']}", headers=owner_headers
    ).status_code == 200
    assert not (report_dir / "authorized.pdf").exists()
