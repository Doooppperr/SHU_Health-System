from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

from flask import current_app, g, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import IndicatorDict, Institution, InstitutionReport, Package, ReportIndicator
from app.org import org_bp
from app.services import get_ocr_provider, get_storage_backend
from app.services.indicator_values import IndicatorValueError, evaluate_is_abnormal, normalize_indicator_value, normalize_ocr_indicator_value
from app.services.institution_management import (
    ManagementValidationError, apply_institution_payload, apply_package_payload,
    delete_institution_image, image_payload, institution_payload,
    reorder_institution_images, save_institution_image,
)
from app.services.ocr import mapping_service
from app.services.permissions import ROLE_INSTITUTION_ADMIN, roles_required
from app.services.record_files import delete_report_urls
from app.services.reports import find_subject_user, submit_report


UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def managed_institution():
    item = db.session.get(Institution, g.current_user.managed_institution_id)
    if item is None or not item.is_active:
        return None, ({"message": "managed institution is unavailable"}, 403)
    return item, None


def parse_date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def resolve_package(institution_id, raw_id):
    if raw_id in {None, ""}:
        return None, None
    try:
        package_id = int(raw_id)
    except (TypeError, ValueError):
        return None, ({"message": "package_id must be an integer"}, 400)
    package = Package.query.filter_by(id=package_id, institution_id=institution_id, is_active=True).first()
    return (package, None) if package else (None, ({"message": "package not found"}, 404))


def scoped_report(report_id):
    institution, error = managed_institution()
    if error:
        return None, error
    report = InstitutionReport.query.filter_by(id=report_id, institution_id=institution.id).first()
    return (report, None) if report else (None, ({"message": "report not found"}, 404))


def create_report_from_payload(payload, *, temporary_file_url=None, diagnostics=None):
    institution, error = managed_institution()
    if error:
        return None, error
    name = (payload.get("subject_name") or "").strip()
    health_id = (payload.get("subject_health_id") or "").strip().upper()
    exam_date = parse_date(payload.get("exam_date"))
    if not name or not health_id or not exam_date:
        return None, ({"message": "subject_name, subject_health_id and exam_date are required"}, 400)
    if not health_id.startswith("HID-"):
        return None, ({"message": "invalid health identity format"}, 400)
    package, package_error = resolve_package(institution.id, payload.get("package_id"))
    if package_error:
        return None, package_error
    report = InstitutionReport(
        institution_id=institution.id,
        created_by_user_id=g.current_user.id,
        created_by_username_snapshot=g.current_user.username,
        subject_name_snapshot=name,
        subject_health_id=health_id,
        exam_date=exam_date,
        package_id=package.id if package else None,
        status="draft",
        temporary_file_url=temporary_file_url,
        ocr_diagnostics=diagnostics,
    )
    db.session.add(report)
    return report, None


@org_bp.get("/dashboard")
@roles_required(ROLE_INSTITUTION_ADMIN)
def dashboard():
    institution, error = managed_institution()
    if error:
        return error
    counts = {status: InstitutionReport.query.filter_by(institution_id=institution.id, status=status).count() for status in ("draft", "locked", "published", "withdrawn")}
    return {"summary": {"institution": institution_payload(institution), "report_status_counts": counts, "active_package_count": Package.query.filter_by(institution_id=institution.id, is_active=True).count()}}, 200


@org_bp.get("/institution")
@roles_required(ROLE_INSTITUTION_ADMIN)
def get_institution():
    item, error = managed_institution()
    return error if error else ({"item": institution_payload(item)}, 200)


@org_bp.put("/institution")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_institution():
    item, error = managed_institution()
    if error:
        return error
    try:
        apply_institution_payload(item, request.get_json(silent=True) or {})
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback()
        return {"message": str(exc)}, 400
    return {"item": institution_payload(item)}, 200


@org_bp.get("/packages")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_packages():
    item, error = managed_institution()
    return error if error else ({"items": [p.to_dict() for p in Package.query.filter_by(institution_id=item.id).order_by(Package.id).all()]}, 200)


@org_bp.post("/packages")
@roles_required(ROLE_INSTITUTION_ADMIN)
def create_package():
    item, error = managed_institution()
    if error:
        return error
    package = Package(institution_id=item.id)
    try:
        apply_package_payload(package, request.get_json(silent=True) or {}, creating=True)
        db.session.add(package); db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback(); return {"message": "package name already exists"}, 409
    return {"item": package.to_dict()}, 201


@org_bp.put("/packages/<int:package_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_package(package_id):
    institution, error = managed_institution()
    if error: return error
    package = Package.query.filter_by(id=package_id, institution_id=institution.id).first()
    if not package: return {"message": "package not found"}, 404
    try:
        apply_package_payload(package, request.get_json(silent=True) or {}); db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 400
    return {"item": package.to_dict()}, 200


@org_bp.delete("/packages/<int:package_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def deactivate_package(package_id):
    institution, error = managed_institution()
    if error: return error
    package = Package.query.filter_by(id=package_id, institution_id=institution.id).first()
    if not package: return {"message": "package not found"}, 404
    package.is_active = False; db.session.commit()
    return {"item": package.to_dict()}, 200


@org_bp.get("/images")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_images():
    item, error = managed_institution(); return error if error else ({"items": [image_payload(i) for i in item.images], "limit": 8}, 200)


@org_bp.post("/images")
@roles_required(ROLE_INSTITUTION_ADMIN)
def upload_image():
    item, error = managed_institution()
    if error: return error
    upload = request.files.get("file")
    if not upload: return {"message": "image file is required"}, 400
    try: image = save_institution_image(item, upload)
    except ManagementValidationError as exc: return {"message": str(exc)}, 400
    return {"item": image_payload(image)}, 201


@org_bp.put("/images/order")
@roles_required(ROLE_INSTITUTION_ADMIN)
def reorder_images():
    item, error = managed_institution()
    if error: return error
    try: images = reorder_institution_images(item.id, (request.get_json(silent=True) or {}).get("image_ids"))
    except ManagementValidationError as exc: db.session.rollback(); return {"message": str(exc)}, 400
    return {"items": [image_payload(i) for i in images]}, 200


@org_bp.delete("/images/<int:image_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def delete_image(image_id):
    item, error = managed_institution()
    if error: return error
    return ({"message": "institution image deleted"}, 200) if delete_institution_image(item.id, image_id) else ({"message": "institution image not found"}, 404)


@org_bp.get("/reports")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_reports():
    institution, error = managed_institution()
    if error: return error
    query = InstitutionReport.query.filter_by(institution_id=institution.id)
    status = (request.args.get("status") or "").strip()
    if status: query = query.filter_by(status=status)
    return {"items": [r.to_dict() for r in query.order_by(InstitutionReport.exam_date.desc(), InstitutionReport.id.desc()).all()]}, 200


@org_bp.post("/reports")
@roles_required(ROLE_INSTITUTION_ADMIN)
def create_report():
    report, error = create_report_from_payload(request.get_json(silent=True) or {})
    if error: return error
    try: db.session.commit()
    except IntegrityError: db.session.rollback(); return {"message": "an active report already exists for this subject and date"}, 409
    return {"item": report.to_dict(include_indicators=True)}, 201


@org_bp.get("/reports/<int:report_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def get_report(report_id):
    report, error = scoped_report(report_id)
    return error if error else ({"item": report.to_dict(include_indicators=True)}, 200)


@org_bp.put("/reports/<int:report_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_report(report_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked reports are immutable"}, 409
    payload = request.get_json(silent=True) or {}
    if "subject_name" in payload: report.subject_name_snapshot = (payload.get("subject_name") or "").strip()
    if "subject_health_id" in payload: report.subject_health_id = (payload.get("subject_health_id") or "").strip().upper()
    if "exam_date" in payload:
        parsed = parse_date(payload.get("exam_date"))
        if not parsed: return {"message": "exam_date must be YYYY-MM-DD"}, 400
        report.exam_date = parsed
    if "package_id" in payload:
        package, package_error = resolve_package(report.institution_id, payload.get("package_id"))
        if package_error: return package_error
        report.package_id = package.id if package else None
    try: db.session.commit()
    except IntegrityError: db.session.rollback(); return {"message": "report update conflicts with an existing active report"}, 409
    return {"item": report.to_dict(include_indicators=True)}, 200


@org_bp.post("/reports/<int:report_id>/indicators")
@roles_required(ROLE_INSTITUTION_ADMIN)
def add_indicator(report_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked reports are immutable"}, 409
    payload = request.get_json(silent=True) or {}
    definition = db.session.get(IndicatorDict, payload.get("indicator_dict_id"))
    if not definition: return {"message": "indicator not found"}, 404
    try: value = normalize_indicator_value(definition, payload.get("value"))
    except IndicatorValueError as exc: return {"message": str(exc)}, 400
    row = ReportIndicator(report_id=report.id, indicator_dict_id=definition.id, value=value, is_abnormal=evaluate_is_abnormal(definition, value), input_source=payload.get("input_source") if payload.get("input_source") in {"manual", "ocr"} else "manual")
    db.session.add(row)
    try: db.session.commit()
    except IntegrityError: db.session.rollback(); return {"message": "indicator already exists in report"}, 409
    return {"item": row.to_dict()}, 201


@org_bp.put("/reports/<int:report_id>/indicators/<int:indicator_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_indicator(report_id, indicator_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked reports are immutable"}, 409
    row = ReportIndicator.query.filter_by(id=indicator_id, report_id=report.id).first()
    if not row: return {"message": "indicator not found"}, 404
    payload = request.get_json(silent=True) or {}
    definition = db.session.get(IndicatorDict, payload.get("indicator_dict_id", row.indicator_dict_id))
    if not definition: return {"message": "indicator not found"}, 404
    try: value = normalize_indicator_value(definition, payload.get("value", row.value))
    except IndicatorValueError as exc: return {"message": str(exc)}, 400
    row.indicator_dict_id = definition.id; row.value = value; row.is_abnormal = evaluate_is_abnormal(definition, value)
    try: db.session.commit()
    except IntegrityError: db.session.rollback(); return {"message": "indicator already exists in report"}, 409
    return {"item": row.to_dict()}, 200


@org_bp.delete("/reports/<int:report_id>/indicators/<int:indicator_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def delete_indicator(report_id, indicator_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked reports are immutable"}, 409
    row = ReportIndicator.query.filter_by(id=indicator_id, report_id=report.id).first()
    if not row: return {"message": "indicator not found"}, 404
    db.session.delete(row); db.session.commit(); return {"message": "indicator deleted"}, 200


@org_bp.post("/reports/ocr")
@roles_required(ROLE_INSTITUTION_ADMIN)
def ocr_report():
    upload = request.files.get("file")
    if not upload or not upload.filename: return {"message": "file is required"}, 400
    if os.path.splitext(upload.filename)[1].lower() not in UPLOAD_EXTENSIONS: return {"message": "unsupported file type"}, 400
    storage = get_storage_backend(current_app.config)
    saved = storage.save(upload, subdir="reports")
    try:
        result = get_ocr_provider(current_app.config).parse_report(saved["abs_path"])
        mapping = mapping_service.map_fields(result.get("fields", []), IndicatorDict.query.all())
        diagnostics = {"engine": result.get("engine"), "parser_version": result.get("parser_version"), **mapping.get("diagnostics", {}), "unmatched": mapping.get("unmatched", [])[:30]}
        report, error = create_report_from_payload(request.form, temporary_file_url=saved["url"], diagnostics=diagnostics)
        if error: storage.delete(saved["key"]); return error
        for candidate in mapping.get("candidate_mappings", []):
            if candidate.get("requires_review"): continue
            definition = db.session.get(IndicatorDict, candidate["indicator_dict_id"])
            try: value = normalize_ocr_indicator_value(definition, candidate["value"])
            except IndicatorValueError: continue
            report.indicators.append(ReportIndicator(indicator_dict_id=definition.id, value=value, is_abnormal=evaluate_is_abnormal(definition, value), input_source="ocr"))
        db.session.commit()
    except Exception:
        db.session.rollback(); storage.delete(saved["key"]); raise
    return {"item": report.to_dict(include_indicators=True), "ocr": {"candidate_mappings": mapping.get("candidate_mappings", []), "diagnostics": diagnostics}}, 201


@org_bp.post("/reports/<int:report_id>/lock")
@roles_required(ROLE_INSTITUTION_ADMIN)
def lock_report(report_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "only draft reports can be locked"}, 409
    if not report.indicators: return {"message": "at least one indicator is required"}, 400
    if find_subject_user(report) is None:
        return {"message": "registered user not found or identity does not match"}, 409
    temp_url = report.temporary_file_url
    report.status = "locked"; report.locked_at = datetime.now(timezone.utc); report.temporary_file_url = None
    if report.ocr_diagnostics: report.ocr_diagnostics = {key: value for key, value in report.ocr_diagnostics.items() if key not in {"raw_text", "fields", "provider_response"}}
    db.session.commit(); delete_report_urls([temp_url])
    return {"item": report.to_dict(include_indicators=True)}, 200


@org_bp.post("/reports/<int:report_id>/submit")
@roles_required(ROLE_INSTITUTION_ADMIN)
def submit(report_id):
    report, error = scoped_report(report_id)
    if error: return error
    try: submit_report(report); db.session.commit()
    except ValueError as exc: db.session.rollback(); return {"message": str(exc)}, 409
    except IntegrityError: db.session.rollback(); return {"message": "report publishing conflict; reload and retry"}, 409
    db.session.refresh(report)
    return {"item": report.to_dict(include_indicators=True), "match_result": "matched"}, 200


@org_bp.post("/reports/<int:report_id>/withdraw")
@roles_required(ROLE_INSTITUTION_ADMIN)
def withdraw(report_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status not in {"locked", "published"}: return {"message": "report cannot be withdrawn from its current status"}, 409
    report.status = "withdrawn"; report.withdrawn_at = datetime.now(timezone.utc)
    db.session.commit(); return {"item": report.to_dict(include_indicators=True)}, 200
