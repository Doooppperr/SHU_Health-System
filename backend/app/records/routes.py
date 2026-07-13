import json
import os
from datetime import date
from uuid import uuid4

from flask import current_app, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required, verify_jwt_in_request
from sqlalchemy import func, update

from app.extensions import db
from app.models import (
    FriendRelation,
    HealthIndicator,
    HealthRecord,
    IndicatorDict,
    Institution,
    Package,
    User,
)
from app.records import records_bp
from app.services import get_ocr_provider, get_storage_backend
from app.services.indicator_values import (
    IndicatorValueError,
    evaluate_is_abnormal,
    normalize_indicator_value,
    normalize_ocr_indicator_value,
)
from app.services.ocr import mapping_service
from app.services.permissions import ROLE_USER, role_error
from app.services.record_files import delete_report_urls, report_file_path


ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
PENDING_ATTACHMENT_KEY = "_pending_attachment"


@records_bp.before_request
def _require_regular_user_for_records():
    verify_jwt_in_request()
    return role_error(_current_user(), ROLE_USER)


def _current_user_id() -> int:
    return int(get_jwt_identity())


def _current_user():
    return db.session.get(User, _current_user_id())


def _parse_exam_date(raw_value: str):
    try:
        return date.fromisoformat(raw_value)
    except (TypeError, ValueError):
        return None


def _parse_optional_int(raw_value):
    if raw_value is None or raw_value == "":
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _parse_owner_id(raw_owner_id, current_user_id: int):
    if raw_owner_id is None or raw_owner_id == "":
        return current_user_id, None

    try:
        owner_id = int(raw_owner_id)
    except (TypeError, ValueError):
        return None, {"message": "owner_id must be integer"}

    return owner_id, None


def _get_manageable_owner_ids(user: User):
    relation_rows = FriendRelation.query.filter_by(user_id=user.id, auth_status=True).all()
    owner_ids = [user.id]
    owner_ids.extend(item.friend_user_id for item in relation_rows)
    return list(dict.fromkeys(owner_ids))


def _validate_owner_manage_permission(manager_user: User, owner_id: int):
    owner_user = db.session.get(User, owner_id)
    if owner_user is None:
        return {"message": "owner user not found"}, 404

    if manager_user.id == owner_id:
        return None, None

    relation = FriendRelation.query.filter_by(user_id=manager_user.id, friend_user_id=owner_id).first()
    if relation is None:
        return {"message": "friend relation not found"}, 403

    if not relation.auth_status:
        return {"message": "friend authorization required"}, 403

    return None, None


def _can_manage_owner(user: User, owner_id: int) -> bool:
    if user.id == owner_id:
        return True

    relation = FriendRelation.query.filter_by(
        user_id=user.id,
        friend_user_id=owner_id,
        auth_status=True,
    ).first()
    return relation is not None


def _get_accessible_record(record_id: int, user: User):
    record = db.session.get(HealthRecord, record_id)
    if record is None:
        return None

    if not _can_manage_owner(user, record.owner_id):
        return None

    return record


def _validate_institution_package(institution_id, package_id):
    institution = db.session.get(Institution, institution_id) if institution_id else None
    if institution_id and institution is None:
        return None, None, {"message": "institution not found"}, 404
    if institution is not None and not getattr(institution, "is_active", True):
        return None, None, {"message": "institution is inactive"}, 400

    package = db.session.get(Package, package_id) if package_id else None
    if package_id and package is None:
        return None, None, {"message": "package not found"}, 404
    if package is not None and not getattr(package, "is_active", True):
        return None, None, {"message": "package is inactive"}, 400

    if package and institution and package.institution_id != institution.id:
        return None, None, {"message": "package does not belong to the institution"}, 400

    if package and institution is None:
        institution = db.session.get(Institution, package.institution_id)
        if institution is None:
            return None, None, {"message": "institution not found"}, 404
        if not getattr(institution, "is_active", True):
            return None, None, {"message": "institution is inactive"}, 400

    return institution, package, None, None


def _build_ocr_snapshot(ocr_result: dict, mapping: dict):
    return {
        "engine": ocr_result.get("engine", "unknown"),
        "raw_text": ocr_result.get("raw_text"),
        "fields": ocr_result.get("fields", []),
        "meta": ocr_result.get("meta", {}),
        "mapping": {
            "candidate_mappings": mapping.get("candidate_mappings", []),
            "unmatched": mapping.get("unmatched", []),
            "filtered": mapping.get("filtered", []),
            "diagnostics": mapping.get("diagnostics", {}),
        },
    }


def _parse_ocr_snapshot(raw_text):
    if not raw_text:
        return {}

    try:
        payload = json.loads(raw_text)
        return payload if isinstance(payload, dict) else {}
    except (TypeError, ValueError):
        return {}


def _load_ocr_snapshot(record: HealthRecord):
    return _parse_ocr_snapshot(record.ocr_raw_text)


def _pending_attachment(snapshot: dict):
    pending = snapshot.get(PENDING_ATTACHMENT_KEY) if isinstance(snapshot, dict) else None
    if not isinstance(pending, dict):
        return None
    report_file_url = pending.get("report_file_url")
    if not isinstance(report_file_url, str) or not report_file_url.strip():
        return None
    return pending


def _normalize_confirmed_mappings(raw_confirmed_mappings):
    if raw_confirmed_mappings is None:
        return None

    if not isinstance(raw_confirmed_mappings, list):
        raise ValueError("confirmed_mappings must be a list")

    deduped = {}
    for item in raw_confirmed_mappings:
        if not isinstance(item, dict):
            continue

        if item.get("ignored"):
            continue

        indicator_dict_id = _parse_optional_int(item.get("indicator_dict_id"))
        value = item.get("value")
        if indicator_dict_id is None or value is None or str(value).strip() == "":
            continue

        deduped[indicator_dict_id] = {
            "indicator_dict_id": indicator_dict_id,
            "value": str(value).strip(),
            "score": float(item.get("score", 1.0) or 1.0),
        }

    return list(deduped.values())


def _resolve_auto_confirmed_mappings(record: HealthRecord):
    snapshot = _load_ocr_snapshot(record)
    mapping_payload = snapshot.get("mapping") or {}
    candidate_mappings = mapping_payload.get("candidate_mappings") or []

    try:
        threshold = float(current_app.config.get("OCR_AUTO_CONFIRM_MIN_SCORE", 0.92))
    except (TypeError, ValueError):
        threshold = 0.92

    best_by_indicator = {}
    for candidate in candidate_mappings:
        if not isinstance(candidate, dict):
            continue

        indicator_dict_id = _parse_optional_int(candidate.get("indicator_dict_id"))
        value = candidate.get("value")

        try:
            score = float(candidate.get("score", 0) or 0)
        except (TypeError, ValueError):
            score = 0.0

        if indicator_dict_id is None or value is None or str(value).strip() == "":
            continue
        if score < threshold:
            continue

        current = best_by_indicator.get(indicator_dict_id)
        if current is None or score > current["score"]:
            best_by_indicator[indicator_dict_id] = {
                "indicator_dict_id": indicator_dict_id,
                "value": str(value).strip(),
                "score": score,
            }

    return list(best_by_indicator.values()), threshold


def _upsert_ocr_indicators(record: HealthRecord, confirmed_mappings):
    if not confirmed_mappings:
        return 0, [], []

    indicator_ids = list({item["indicator_dict_id"] for item in confirmed_mappings})
    dict_rows = IndicatorDict.query.filter(IndicatorDict.id.in_(indicator_ids)).all()
    dict_map = {item.id: item for item in dict_rows}

    invalid_ids = [item_id for item_id in indicator_ids if item_id not in dict_map]
    invalid_values = []

    existing_rows = HealthIndicator.query.filter(
        HealthIndicator.record_id == record.id,
        HealthIndicator.indicator_dict_id.in_(indicator_ids),
    ).all()
    existing_map = {item.indicator_dict_id: item for item in existing_rows}

    changed_count = 0

    for mapping in confirmed_mappings:
        indicator_dict_id = mapping["indicator_dict_id"]
        indicator_dict = dict_map.get(indicator_dict_id)
        if indicator_dict is None:
            continue

        try:
            value = normalize_ocr_indicator_value(indicator_dict, mapping["value"])
        except IndicatorValueError as exc:
            invalid_values.append(
                {
                    "indicator_dict_id": indicator_dict_id,
                    "message": str(exc),
                }
            )
            continue
        is_abnormal = evaluate_is_abnormal(indicator_dict, value)

        existing = existing_map.get(indicator_dict_id)
        if existing is not None:
            existing.value = value
            existing.is_abnormal = is_abnormal
            existing.source = "ocr"
            changed_count += 1
            continue

        db.session.add(
            HealthIndicator(
                record_id=record.id,
                indicator_dict_id=indicator_dict_id,
                value=value,
                is_abnormal=is_abnormal,
                source="ocr",
            )
        )
        changed_count += 1

    return changed_count, invalid_ids, invalid_values


@records_bp.get("/summary")
@jwt_required()
def get_record_summary():
    user = _current_user()
    owner_ids = _get_manageable_owner_ids(user)
    base = HealthRecord.query.filter(HealthRecord.owner_id.in_(owner_ids))
    record_count = base.count()
    confirmed_count = base.filter(HealthRecord.status == "confirmed").count()
    recent = base.order_by(HealthRecord.exam_date.desc(), HealthRecord.id.desc()).limit(5).all()
    recent_exam_date = (
        db.session.query(func.max(HealthRecord.exam_date))
        .filter(HealthRecord.owner_id.in_(owner_ids))
        .scalar()
    )
    abnormal_count = (
        db.session.query(func.count(HealthIndicator.id))
        .join(HealthRecord, HealthIndicator.record_id == HealthRecord.id)
        .filter(
            HealthRecord.owner_id.in_(owner_ids),
            HealthRecord.status == "confirmed",
            HealthIndicator.is_abnormal.is_(True),
        )
        .scalar()
        or 0
    )
    return {
        "summary": {
            "record_count": record_count,
            "confirmed_count": confirmed_count,
            "abnormal_indicator_count": abnormal_count,
            "recent_exam_date": recent_exam_date.isoformat() if recent_exam_date else None,
        },
        "recent_records": [item.to_dict(include_indicators=False) for item in recent],
    }, 200


@records_bp.get("")
@jwt_required()
def list_records():
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    owner_ids = _get_manageable_owner_ids(user)
    records = (
        HealthRecord.query.filter(HealthRecord.owner_id.in_(owner_ids))
        .order_by(HealthRecord.exam_date.desc(), HealthRecord.id.desc())
        .all()
    )

    manageable_set = set(owner_ids)
    items = []
    for record in records:
        payload = record.to_dict(include_indicators=False)
        payload["is_owner"] = record.owner_id == user.id
        payload["can_manage"] = record.owner_id in manageable_set
        items.append(payload)

    return {"items": items}, 200


@records_bp.post("")
@jwt_required()
def create_record():
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    payload = request.get_json(silent=True) or {}

    if payload.get("report_file_url") not in {None, ""}:
        return {
            "message": "report_file_url is managed by the server; upload the report file instead"
        }, 400

    exam_date = _parse_exam_date(payload.get("exam_date"))
    if exam_date is None:
        return {"message": "exam_date is required and must be ISO date (YYYY-MM-DD)"}, 400

    owner_id, owner_parse_error = _parse_owner_id(payload.get("owner_id"), user.id)
    if owner_parse_error:
        return owner_parse_error, 400

    owner_error_payload, owner_error_status = _validate_owner_manage_permission(user, owner_id)
    if owner_error_payload:
        return owner_error_payload, owner_error_status

    raw_institution_id = payload.get("institution_id")
    raw_package_id = payload.get("package_id")
    institution_id = _parse_optional_int(raw_institution_id)
    package_id = _parse_optional_int(raw_package_id)
    if raw_institution_id not in {None, ""} and institution_id is None:
        return {"message": "institution_id must be integer"}, 400
    if raw_package_id not in {None, ""} and package_id is None:
        return {"message": "package_id must be integer"}, 400

    institution, package, error_payload, error_status = _validate_institution_package(institution_id, package_id)
    if error_payload:
        return error_payload, error_status

    status = payload.get("status") or "confirmed"
    if status not in {"draft", "parsed", "confirmed"}:
        return {"message": "invalid status"}, 400

    record = HealthRecord(
        owner_id=owner_id,
        uploader_id=user.id,
        institution_id=institution.id if institution else None,
        package_id=package.id if package else None,
        exam_date=exam_date,
        status=status,
    )
    db.session.add(record)
    db.session.commit()

    return {"item": record.to_dict(include_indicators=True)}, 201


@records_bp.post("/upload")
@jwt_required()
def upload_record_and_parse():
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    raw_record_id = request.form.get("record_id")
    target_record = None
    target_snapshot_text = None
    if raw_record_id not in {None, ""}:
        record_id = _parse_optional_int(raw_record_id)
        if record_id is None or record_id <= 0:
            return {"message": "record_id must be a positive integer"}, 400
        target_record = _get_accessible_record(record_id, user)
        if target_record is None:
            return {"message": "record not found"}, 404
        target_snapshot_text = target_record.ocr_raw_text

    uploaded_file = request.files.get("file")

    if uploaded_file is None or not uploaded_file.filename:
        return {"message": "file is required"}, 400

    extension = os.path.splitext(uploaded_file.filename)[1].lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        return {"message": "unsupported file type"}, 400

    if target_record is None:
        exam_date = _parse_exam_date(request.form.get("exam_date"))
        if exam_date is None:
            return {"message": "exam_date is required and must be ISO date (YYYY-MM-DD)"}, 400

        owner_id, owner_parse_error = _parse_owner_id(request.form.get("owner_id"), user.id)
        if owner_parse_error:
            return owner_parse_error, 400

        owner_error_payload, owner_error_status = _validate_owner_manage_permission(user, owner_id)
        if owner_error_payload:
            return owner_error_payload, owner_error_status

        raw_institution_id = request.form.get("institution_id")
        raw_package_id = request.form.get("package_id")
        institution_id = _parse_optional_int(raw_institution_id)
        package_id = _parse_optional_int(raw_package_id)
        if raw_institution_id not in {None, ""} and institution_id is None:
            return {"message": "institution_id must be integer"}, 400
        if raw_package_id not in {None, ""} and package_id is None:
            return {"message": "package_id must be integer"}, 400

        institution, package, error_payload, error_status = _validate_institution_package(
            institution_id,
            package_id,
        )
        if error_payload:
            return error_payload, error_status

    storage = get_storage_backend(current_app.config)
    saved_file = storage.save(uploaded_file, subdir="reports")

    provider = get_ocr_provider(current_app.config)

    try:
        ocr_result = provider.parse_report(saved_file["abs_path"])
    except Exception as exc:
        storage.delete(saved_file["key"])
        return {"message": f"ocr parse failed: {exc}"}, 500

    indicator_dicts = IndicatorDict.query.all()
    if not indicator_dicts:
        current_app.logger.warning("indicator_dicts is empty before OCR mapping, trying seed_indicator_dicts")
        try:
            from app.seed import seed_indicator_dicts

            seed_indicator_dicts()
            indicator_dicts = IndicatorDict.query.all()
        except Exception as exc:
            current_app.logger.exception("seed_indicator_dicts failed: %s", exc)

    if not indicator_dicts:
        storage.delete(saved_file["key"])
        return {
            "message": "indicator dictionary is empty, please initialize seed data and retry",
        }, 503

    try:
        mapping = mapping_service.map_fields(ocr_result.get("fields", []), indicator_dicts)
        ocr_snapshot = _build_ocr_snapshot(ocr_result, mapping)
    except Exception:
        storage.delete(saved_file["key"])
        raise

    attachment_id = None
    if target_record is None:
        record = HealthRecord(
            owner_id=owner_id,
            uploader_id=user.id,
            institution_id=institution.id if institution else None,
            package_id=package.id if package else None,
            exam_date=exam_date,
        )
        db.session.add(record)
        record.uploader_id = user.id
        record.report_file_url = saved_file["url"]
        record.ocr_raw_text = json.dumps(ocr_snapshot, ensure_ascii=False)
        record.status = "parsed"
        previous_pending_report_url = None
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            storage.delete(saved_file["key"])
            raise
    else:
        db.session.expire_all()
        record = _get_accessible_record(record_id, user)
        if record is None:
            storage.delete(saved_file["key"])
            return {"message": "record not found"}, 404

        existing_snapshot = _parse_ocr_snapshot(target_snapshot_text)
        existing_pending = _pending_attachment(existing_snapshot)
        previous_pending_report_url = (
            existing_pending.get("report_file_url") if existing_pending else None
        )
        previous_ocr_raw_text = (
            existing_pending.get("previous_ocr_raw_text")
            if existing_pending
            else target_snapshot_text
        )
        attachment_id = uuid4().hex
        ocr_snapshot[PENDING_ATTACHMENT_KEY] = {
            "attachment_id": attachment_id,
            "report_file_url": saved_file["url"],
            "uploader_id": user.id,
            "previous_ocr_raw_text": previous_ocr_raw_text,
        }
        snapshot_condition = (
            HealthRecord.ocr_raw_text.is_(None)
            if target_snapshot_text is None
            else HealthRecord.ocr_raw_text == target_snapshot_text
        )
        result = db.session.execute(
            update(HealthRecord)
            .where(HealthRecord.id == record.id, snapshot_condition)
            .values(ocr_raw_text=json.dumps(ocr_snapshot, ensure_ascii=False))
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.session.rollback()
            storage.delete(saved_file["key"])
            return {
                "message": "record changed while OCR was processing; upload again",
                "code": "OCR_ATTACHMENT_CONFLICT",
            }, 409
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            storage.delete(saved_file["key"])
            raise
        db.session.expire_all()
        record = db.session.get(HealthRecord, record_id)

    if previous_pending_report_url and previous_pending_report_url != saved_file["url"]:
        delete_report_urls([previous_pending_report_url])

    return {
        "item": record.to_dict(include_indicators=True),
        "ocr": {
            "provider": ocr_result.get("engine", "unknown"),
            "mapped_count": len(mapping["mapped"]),
            "unmatched_count": len(mapping["unmatched"]),
            "unmatched_fields": mapping["unmatched"],
            "filtered_count": len(mapping.get("filtered", [])),
            "filtered_fields": mapping.get("filtered", []),
            "candidate_mappings": mapping.get("candidate_mappings", []),
            "diagnostics": mapping.get("diagnostics", {}),
            "pending_confirmation": target_record is not None,
            "attachment_id": attachment_id,
        },
    }, 200 if target_record is not None else 201


@records_bp.put("/<int:record_id>/confirm")
@jwt_required()
def confirm_record(record_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    record = _get_accessible_record(record_id, user)
    if record is None:
        return {"message": "record not found"}, 404

    snapshot_text = record.ocr_raw_text
    snapshot = _parse_ocr_snapshot(snapshot_text)
    pending_attachment = _pending_attachment(snapshot)

    if record.status == "confirmed" and pending_attachment is None:
        return {"item": record.to_dict(include_indicators=True), "message": "already confirmed"}, 200

    if pending_attachment is None and record.status not in {"draft", "parsed"}:
        return {"message": "record status cannot be confirmed"}, 400

    payload = request.get_json(silent=True) or {}
    if pending_attachment is not None:
        requested_attachment_id = payload.get("attachment_id") if isinstance(payload, dict) else None
        if (
            not isinstance(requested_attachment_id, str)
            or requested_attachment_id != pending_attachment.get("attachment_id")
        ):
            return {
                "message": "OCR attachment is stale; reload the pending OCR result",
                "code": "OCR_ATTACHMENT_STALE",
            }, 409
    raw_confirmed_mappings = payload.get("confirmed_mappings") if isinstance(payload, dict) else None

    try:
        confirmed_mappings = _normalize_confirmed_mappings(raw_confirmed_mappings)
    except ValueError as exc:
        return {"message": str(exc)}, 400

    if confirmed_mappings is None:
        confirmed_mappings, auto_threshold = _resolve_auto_confirmed_mappings(record)
        confirm_source = "auto_high_confidence"
    else:
        auto_threshold = None
        confirm_source = "manual_selection"

    changed_count, invalid_ids, invalid_values = _upsert_ocr_indicators(
        record,
        confirmed_mappings,
    )
    if invalid_ids:
        db.session.rollback()
        return {"message": "some indicator_dict_id are invalid", "invalid_indicator_dict_ids": invalid_ids}, 400
    if invalid_values:
        db.session.rollback()
        return {
            "message": "some indicator values are invalid",
            "invalid_indicator_values": invalid_values,
        }, 400

    previous_report_url = None
    if pending_attachment is not None:
        previous_report_url = record.report_file_url
        pending_uploader_id = _parse_optional_int(pending_attachment.get("uploader_id"))
        if pending_uploader_id is None:
            db.session.rollback()
            return {"message": "pending OCR attachment is invalid"}, 409
        snapshot.pop(PENDING_ATTACHMENT_KEY, None)
        result = db.session.execute(
            update(HealthRecord)
            .where(
                HealthRecord.id == record.id,
                HealthRecord.ocr_raw_text == snapshot_text,
            )
            .values(
                report_file_url=pending_attachment["report_file_url"],
                uploader_id=pending_uploader_id,
                ocr_raw_text=json.dumps(snapshot, ensure_ascii=False),
                status="confirmed",
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.session.rollback()
            return {
                "message": "OCR attachment changed; reload before confirming",
                "code": "OCR_ATTACHMENT_STALE",
            }, 409
    else:
        record.status = "confirmed"

    db.session.commit()
    db.session.expire_all()
    record = db.session.get(HealthRecord, record_id)

    if previous_report_url and previous_report_url != record.report_file_url:
        delete_report_urls([previous_report_url])

    response_payload = {
        "item": record.to_dict(include_indicators=True),
        "message": "record confirmed",
        "ocr": {
            "confirm_source": confirm_source,
            "confirmed_count": changed_count,
        },
    }
    if auto_threshold is not None:
        response_payload["ocr"]["auto_threshold"] = auto_threshold

    return response_payload, 200


@records_bp.get("/<int:record_id>/ocr-pending")
@jwt_required()
def get_pending_ocr_attachment(record_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    record = _get_accessible_record(record_id, user)
    if record is None:
        return {"message": "record not found"}, 404

    snapshot = _load_ocr_snapshot(record)
    pending = _pending_attachment(snapshot)
    attachment_id = pending.get("attachment_id") if pending else None
    if not isinstance(attachment_id, str) or not attachment_id:
        return {"message": "pending OCR attachment not found"}, 404

    mapping = snapshot.get("mapping") or {}
    candidates = mapping.get("candidate_mappings") or []
    unmatched = mapping.get("unmatched") or []
    filtered = mapping.get("filtered") or []
    return {
        "item": record.to_dict(include_indicators=True),
        "ocr": {
            "provider": snapshot.get("engine", "unknown"),
            "mapped_count": len(candidates),
            "unmatched_count": len(unmatched),
            "unmatched_fields": unmatched,
            "filtered_count": len(filtered),
            "filtered_fields": filtered,
            "candidate_mappings": candidates,
            "diagnostics": mapping.get("diagnostics", {}),
            "pending_confirmation": True,
            "attachment_id": attachment_id,
        },
    }, 200


@records_bp.delete("/<int:record_id>/ocr-pending")
@jwt_required()
def cancel_pending_ocr_attachment(record_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    record = _get_accessible_record(record_id, user)
    if record is None:
        return {"message": "record not found"}, 404

    snapshot_text = record.ocr_raw_text
    snapshot = _parse_ocr_snapshot(snapshot_text)
    pending = _pending_attachment(snapshot)
    if pending is None:
        return {"message": "pending OCR attachment not found"}, 404

    payload = request.get_json(silent=True) or {}
    attachment_id = payload.get("attachment_id") if isinstance(payload, dict) else None
    if not isinstance(attachment_id, str) or attachment_id != pending.get("attachment_id"):
        return {
            "message": "OCR attachment is stale; reload before cancelling",
            "code": "OCR_ATTACHMENT_STALE",
        }, 409

    previous_ocr_raw_text = pending.get("previous_ocr_raw_text")
    if previous_ocr_raw_text is not None and not isinstance(previous_ocr_raw_text, str):
        previous_ocr_raw_text = None
    result = db.session.execute(
        update(HealthRecord)
        .where(
            HealthRecord.id == record.id,
            HealthRecord.ocr_raw_text == snapshot_text,
        )
        .values(ocr_raw_text=previous_ocr_raw_text)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.session.rollback()
        return {
            "message": "OCR attachment changed; reload before cancelling",
            "code": "OCR_ATTACHMENT_STALE",
        }, 409

    pending_report_file_url = pending["report_file_url"]
    db.session.commit()
    delete_report_urls([pending_report_file_url])
    db.session.expire_all()
    record = db.session.get(HealthRecord, record_id)
    return {
        "item": record.to_dict(include_indicators=True),
        "message": "pending OCR attachment cancelled",
    }, 200


@records_bp.get("/<int:record_id>")
@jwt_required()
def get_record_detail(record_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    record = _get_accessible_record(record_id, user)
    if record is None:
        return {"message": "record not found"}, 404

    return {"item": record.to_dict(include_indicators=True)}, 200


@records_bp.get("/<int:record_id>/file")
@jwt_required()
def download_record_file(record_id: int):
    user = _current_user()
    record = _get_accessible_record(record_id, user)
    if record is None:
        return {"message": "record not found"}, 404
    report_path = report_file_path(record.report_file_url)
    if report_path is None:
        return {"message": "report file not found"}, 404
    return send_file(report_path, as_attachment=True, download_name=report_path.name)


@records_bp.put("/<int:record_id>")
@jwt_required()
def update_record(record_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    record = _get_accessible_record(record_id, user)
    if record is None:
        return {"message": "record not found"}, 404

    payload = request.get_json(silent=True) or {}

    if "exam_date" in payload:
        exam_date = _parse_exam_date(payload.get("exam_date"))
        if exam_date is None:
            return {"message": "exam_date must be ISO date (YYYY-MM-DD)"}, 400
        record.exam_date = exam_date

    if "owner_id" in payload:
        owner_id = _parse_optional_int(payload.get("owner_id"))
        if owner_id is None:
            return {"message": "owner_id must be integer"}, 400
        if owner_id != record.owner_id:
            return {
                "message": "record owner cannot be changed through the regular user API"
            }, 403

    institution_id = record.institution_id
    package_id = record.package_id
    source_fields_changed = "institution_id" in payload or "package_id" in payload

    if "institution_id" in payload:
        raw_institution_id = payload.get("institution_id")
        if raw_institution_id in {None, ""}:
            institution_id = None
            package_id = None
        else:
            institution_id = _parse_optional_int(raw_institution_id)
            if institution_id is None:
                return {"message": "institution_id must be integer"}, 400
            if institution_id != record.institution_id and "package_id" not in payload:
                package_id = None

    if "package_id" in payload:
        raw_package_id = payload.get("package_id")
        if raw_package_id in {None, ""}:
            package_id = None
        else:
            package_id = _parse_optional_int(raw_package_id)
            if package_id is None:
                return {"message": "package_id must be integer"}, 400

    if source_fields_changed:
        institution, package, error_payload, error_status = _validate_institution_package(
            institution_id,
            package_id,
        )
        if error_payload:
            return error_payload, error_status

        record.institution_id = institution.id if institution else None
        record.package_id = package.id if package else None

    if "status" in payload:
        status = payload.get("status")
        if status not in {"draft", "parsed", "confirmed"}:
            return {"message": "invalid status"}, 400
        record.status = status

    db.session.commit()
    return {"item": record.to_dict(include_indicators=True)}, 200


@records_bp.delete("/<int:record_id>")
@jwt_required()
def delete_record(record_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    record = _get_accessible_record(record_id, user)
    if record is None:
        return {"message": "record not found"}, 404

    snapshot_text = record.ocr_raw_text
    report_file_url = record.report_file_url
    pending = _pending_attachment(_load_ocr_snapshot(record))
    pending_report_file_url = pending.get("report_file_url") if pending else None

    # Join the same optimistic-concurrency protocol used by OCR attachment
    # uploads.  The no-op UPDATE both verifies that the snapshot we inspected is
    # still current and acquires the database write/row lock for the remainder
    # of this transaction.  Therefore either deletion wins (and a concurrent
    # uploader later removes its newly saved file after its CAS fails), or the
    # upload wins and deletion asks the client to reload before retrying.
    snapshot_condition = (
        HealthRecord.ocr_raw_text.is_(None)
        if snapshot_text is None
        else HealthRecord.ocr_raw_text == snapshot_text
    )
    lock_result = db.session.execute(
        update(HealthRecord)
        .where(HealthRecord.id == record.id, snapshot_condition)
        .values(ocr_raw_text=HealthRecord.ocr_raw_text)
        .execution_options(synchronize_session=False)
    )
    if lock_result.rowcount != 1:
        db.session.rollback()
        return {
            "message": "record changed while it was being deleted; reload and retry",
            "code": "RECORD_DELETE_CONFLICT",
        }, 409

    db.session.delete(record)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    delete_report_urls([report_file_url, pending_report_file_url])
    return {"message": "record deleted"}, 200


@records_bp.post("/<int:record_id>/indicators")
@jwt_required()
def add_record_indicator(record_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    record = _get_accessible_record(record_id, user)
    if record is None:
        return {"message": "record not found"}, 404

    payload = request.get_json(silent=True) or {}
    indicator_dict_id = _parse_optional_int(payload.get("indicator_dict_id"))
    value = payload.get("value")

    if indicator_dict_id is None or value is None or str(value).strip() == "":
        return {"message": "indicator_dict_id and value are required"}, 400

    indicator_dict = db.session.get(IndicatorDict, indicator_dict_id)
    if indicator_dict is None:
        return {"message": "indicator dict not found"}, 404

    existing = HealthIndicator.query.filter_by(record_id=record.id, indicator_dict_id=indicator_dict_id).first()
    if existing is not None:
        return {"message": "indicator already exists in record"}, 409

    try:
        normalized_value = normalize_indicator_value(indicator_dict, value)
    except IndicatorValueError as exc:
        return {"message": str(exc)}, 400

    indicator = HealthIndicator(
        record_id=record.id,
        indicator_dict_id=indicator_dict_id,
        value=normalized_value,
        is_abnormal=evaluate_is_abnormal(indicator_dict, normalized_value),
        source="manual",
    )
    db.session.add(indicator)
    db.session.commit()

    return {"item": indicator.to_dict()}, 201


@records_bp.put("/<int:record_id>/indicators/<int:indicator_id>")
@jwt_required()
def update_record_indicator(record_id: int, indicator_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    record = _get_accessible_record(record_id, user)
    if record is None:
        return {"message": "record not found"}, 404

    indicator = HealthIndicator.query.filter_by(id=indicator_id, record_id=record.id).first()
    if indicator is None:
        return {"message": "indicator not found"}, 404

    payload = request.get_json(silent=True) or {}
    value = payload.get("value", indicator.value)
    indicator_dict_id = _parse_optional_int(payload.get("indicator_dict_id")) or indicator.indicator_dict_id

    if value is None or str(value).strip() == "":
        return {"message": "value is required"}, 400

    indicator_dict = db.session.get(IndicatorDict, indicator_dict_id)
    if indicator_dict is None:
        return {"message": "indicator dict not found"}, 404

    duplicate = (
        HealthIndicator.query.filter(
            HealthIndicator.record_id == record.id,
            HealthIndicator.indicator_dict_id == indicator_dict_id,
            HealthIndicator.id != indicator.id,
        ).first()
    )
    if duplicate is not None:
        return {"message": "indicator already exists in record"}, 409

    try:
        normalized_value = normalize_indicator_value(indicator_dict, value)
    except IndicatorValueError as exc:
        return {"message": str(exc)}, 400

    indicator.indicator_dict_id = indicator_dict_id
    indicator.value = normalized_value
    indicator.is_abnormal = evaluate_is_abnormal(indicator_dict, normalized_value)

    db.session.commit()
    return {"item": indicator.to_dict()}, 200


@records_bp.delete("/<int:record_id>/indicators/<int:indicator_id>")
@jwt_required()
def delete_record_indicator(record_id: int, indicator_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    record = _get_accessible_record(record_id, user)
    if record is None:
        return {"message": "record not found"}, 404

    indicator = HealthIndicator.query.filter_by(id=indicator_id, record_id=record.id).first()
    if indicator is None:
        return {"message": "indicator not found"}, 404

    db.session.delete(indicator)
    db.session.commit()
    return {"message": "indicator deleted"}, 200
