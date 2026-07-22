from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app, g, request, send_file
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Appointment, AppointmentEvent, HealthDomain, IndicatorDict, Institution,
    InstitutionReport, Package, PackageChangeRequest, ReportAsset,
    ReportAccessLog, ReportIndicator, ReportTextResult, WaitlistSubscription,
)
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
from app.services.package_reviews import create_change_request
from app.services.record_files import delete_report_urls
from app.services.reports import find_subject_user, submit_report
from app.services.dates import calendar_date_iso
from app.services.domain_rules import (
    DomainAdmissionError, admit_indicator, report_allowed_domain_ids,
    validate_report_domains,
)


UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


def managed_institution():
    item = db.session.get(Institution, g.current_user.managed_institution_id)
    if item is None or not item.is_active or item.organization is None or not item.organization.is_active:
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


def readable_report(report_id):
    institution, error = managed_institution()
    if error:
        return None, None, error
    report = db.session.get(InstitutionReport, report_id)
    if report is None or report.institution is None:
        return None, None, ({"message": "report not found"}, 404)
    own_branch = report.institution_id == institution.id
    same_organization = report.institution.organization_id == institution.organization_id
    if not own_branch and (not same_organization or report.status != "published"):
        return None, None, ({"message": "report not found"}, 404)
    return report, own_branch, None


def report_payload(report, current_institution, *, include_indicators=False):
    payload = report.to_dict(include_indicators=include_indicators)
    own_branch = report.institution_id == current_institution.id
    payload.update({
        "source_branch": {
            "id": report.institution.id,
            "organization_id": report.institution.organization_id,
            "name": report.institution.organization.name,
            "branch_name": report.institution.branch_name,
        },
        "access_mode": "editable" if own_branch else "cross_branch_read_only",
        "can_edit": own_branch and report.status != "published",
    })
    return payload


def log_cross_branch_access(report, access_type):
    current = g.current_user.managed_institution
    if report.institution_id == current.id:
        return
    db.session.add(ReportAccessLog(
        actor_user_id=g.current_user.id,
        actor_institution_id=current.id,
        report_id=report.id,
        source_institution_id=report.institution_id,
        access_type=access_type,
    ))
    db.session.commit()


def create_report_from_payload(payload, *, temporary_file_url=None, diagnostics=None):
    institution, error = managed_institution()
    if error:
        return None, error
    try:
        appointment_id = int(payload.get("appointment_id"))
    except (TypeError, ValueError):
        return None, ({"message": "appointment_id is required"}, 400)
    appointment = Appointment.query.filter_by(id=appointment_id, institution_id=institution.id).first()
    if appointment is None:
        return None, ({"message": "appointment not found"}, 404)
    if appointment.status != "awaiting_report":
        return None, ({"message": "only appointments awaiting a report can create one"}, 409)
    if appointment.report is not None:
        return None, ({"message": "this appointment already has a report"}, 409)
    report = InstitutionReport(
        institution_id=institution.id,
        appointment_id=appointment.id,
        created_by_user_id=g.current_user.id,
        created_by_username_snapshot=g.current_user.username,
        subject_name_snapshot=appointment.user_name_snapshot,
        subject_health_id=appointment.user_health_id_snapshot,
        exam_date=appointment.appointment_date,
        package_id=appointment.package_id,
        package_version_id=appointment.package_version_id,
        matched_user_id=appointment.user_id,
        status="draft",
        temporary_file_url=temporary_file_url,
        ocr_diagnostics=diagnostics,
    )
    db.session.add(report)
    db.session.flush()
    return report, None


@org_bp.get("/dashboard")
@roles_required(ROLE_INSTITUTION_ADMIN)
def dashboard():
    institution, error = managed_institution()
    if error:
        return error
    counts = {status: InstitutionReport.query.filter_by(institution_id=institution.id, status=status).count() for status in ("draft", "locked", "published")}
    appointment_counts = {
        status: Appointment.query.filter_by(institution_id=institution.id, status=status).count()
        for status in ("unfulfilled", "awaiting_report", "fulfilled", "invalidated", "cancelled")
    }
    today = datetime.now(BUSINESS_TZ).date()
    today_query = Appointment.query.filter_by(institution_id=institution.id, appointment_date=today)
    today_counts = {
        status: today_query.filter_by(status=status).count()
        for status in ("unfulfilled", "awaiting_report", "fulfilled", "invalidated", "cancelled")
    }
    booked = sum(today_counts[status] for status in ("unfulfilled", "awaiting_report", "fulfilled"))
    capacity = institution.daily_appointment_limit
    task_rows = Appointment.query.filter(
        Appointment.institution_id == institution.id,
        Appointment.status.in_(("unfulfilled", "awaiting_report")),
    ).order_by(Appointment.appointment_date, Appointment.id).limit(8).all()
    tasks = [{
        "id": row.id,
        "appointment_date": calendar_date_iso(row.appointment_date),
        "subject_name": row.user_name_snapshot,
        "package_name": row.package_name_snapshot,
        "status": row.status,
        "status_label": "待确认到检" if row.status == "unfulfilled" else "待归档健康数据",
        "next_action": "确认到检" if row.status == "unfulfilled" else "完善健康数据",
        "booking_group_id": row.booking_group_id,
    } for row in task_rows]
    review_rows = PackageChangeRequest.query.filter_by(institution_id=institution.id).order_by(
        PackageChangeRequest.requested_at.desc(), PackageChangeRequest.id.desc()
    ).limit(3).all()
    return {"summary": {
        "institution": institution_payload(institution),
        "report_status_counts": counts,
        "appointment_status_counts": appointment_counts,
        "pending_package_review_count": PackageChangeRequest.query.filter_by(institution_id=institution.id, status="pending").count(),
        "active_package_count": Package.query.filter_by(institution_id=institution.id, is_active=True).count(),
        "today": {
            "date": today.isoformat(), "capacity": capacity, "booked": booked,
            "remaining": None if capacity is None else max(capacity - booked, 0),
            "awaiting_arrival": today_counts["unfulfilled"],
            "awaiting_archive": today_counts["awaiting_report"],
            "completed": today_counts["fulfilled"],
            "waitlist_subscriptions": WaitlistSubscription.query.filter_by(
                institution_id=institution.id, appointment_date=today, status="active"
            ).count(),
        },
        "tasks": tasks,
        "recent_package_reviews": [row.to_dict() for row in review_rows],
    }}, 200


@org_bp.get("/context")
@roles_required(ROLE_INSTITUTION_ADMIN)
def context():
    institution, error = managed_institution()
    if error: return error
    return {"item": {
        "organization": institution.organization.to_dict(),
        "current_branch": institution_payload(institution),
        "sibling_branches": [institution_payload(branch) for branch in institution.organization.branches if branch.id != institution.id and branch.is_active],
        "permissions": {
            "manage_current_branch": True,
            "manage_sibling_branches": False,
            "read_sibling_published_reports": True,
            "read_sibling_drafts": False,
            "read_sibling_appointments": False,
        },
    }}, 200


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


@org_bp.get("/appointment-capacity")
@roles_required(ROLE_INSTITUTION_ADMIN)
def get_appointment_capacity():
    item, error = managed_institution()
    return error if error else ({"daily_appointment_limit": item.daily_appointment_limit, "unlimited": item.daily_appointment_limit is None}, 200)


@org_bp.put("/appointment-capacity")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_appointment_capacity():
    item, error = managed_institution()
    if error:
        return error
    raw = (request.get_json(silent=True) or {}).get("daily_appointment_limit")
    if raw in {None, ""}:
        item.daily_appointment_limit = None
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return {"message": "daily_appointment_limit must be a positive integer or null"}, 400
        if isinstance(raw, bool) or value <= 0:
            return {"message": "daily_appointment_limit must be a positive integer or null"}, 400
        item.daily_appointment_limit = value
    db.session.commit()
    return {"daily_appointment_limit": item.daily_appointment_limit, "unlimited": item.daily_appointment_limit is None}, 200


@org_bp.get("/packages")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_packages():
    item, error = managed_institution()
    if error:
        return error
    rows = []
    for package in Package.query.filter_by(institution_id=item.id).order_by(Package.id).all():
        payload = package.to_dict()
        pending = PackageChangeRequest.query.filter_by(package_id=package.id, status="pending").first()
        payload["pending_request"] = pending.to_dict() if pending else None
        rows.append(payload)
    return {"items": rows}, 200


@org_bp.post("/packages")
@roles_required(ROLE_INSTITUTION_ADMIN)
def create_package():
    item, error = managed_institution()
    if error:
        return error
    try:
        change = create_change_request(item, g.current_user, "create", payload=request.get_json(silent=True) or {})
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 400
    return {"item": change.to_dict(), "message": "套餐新增申请已提交审核"}, 201


@org_bp.put("/packages/<int:package_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_package(package_id):
    institution, error = managed_institution()
    if error: return error
    package = Package.query.filter_by(id=package_id, institution_id=institution.id).first()
    if not package: return {"message": "package not found"}, 404
    try:
        change = create_change_request(institution, g.current_user, "update", package=package, payload=request.get_json(silent=True) or {})
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 400
    return {"item": change.to_dict(), "message": "套餐修改申请已提交审核"}, 202


@org_bp.delete("/packages/<int:package_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def deactivate_package(package_id):
    institution, error = managed_institution()
    if error: return error
    package = Package.query.filter_by(id=package_id, institution_id=institution.id).first()
    if not package: return {"message": "package not found"}, 404
    try:
        change = create_change_request(institution, g.current_user, "deactivate", package=package)
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 400
    return {"item": change.to_dict(), "message": "套餐下架申请已提交审核"}, 202


@org_bp.post("/packages/<int:package_id>/reactivate")
@roles_required(ROLE_INSTITUTION_ADMIN)
def reactivate_package(package_id):
    institution, error = managed_institution()
    if error: return error
    package = Package.query.filter_by(id=package_id, institution_id=institution.id).first()
    if not package: return {"message": "package not found"}, 404
    try:
        change = create_change_request(institution, g.current_user, "reactivate", package=package)
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 400
    return {"item": change.to_dict(), "message": "套餐恢复申请已提交审核"}, 202


@org_bp.get("/package-change-requests")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_package_change_requests():
    institution, error = managed_institution()
    if error: return error
    rows = PackageChangeRequest.query.filter_by(institution_id=institution.id).order_by(PackageChangeRequest.requested_at.desc(), PackageChangeRequest.id.desc()).all()
    return {"items": [item.to_dict() for item in rows]}, 200


@org_bp.post("/package-change-requests/<int:request_id>/withdraw")
@roles_required(ROLE_INSTITUTION_ADMIN)
def withdraw_package_change_request(request_id):
    institution, error = managed_institution()
    if error: return error
    item = PackageChangeRequest.query.filter_by(id=request_id, institution_id=institution.id).first()
    if item is None: return {"message": "review request not found"}, 404
    if item.status != "pending": return {"message": "only pending requests can be withdrawn"}, 409
    item.status = "withdrawn"; item.withdrawn_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"item": item.to_dict()}, 200


@org_bp.get("/appointments")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_appointments():
    institution, error = managed_institution()
    if error: return error
    base = Appointment.query.filter_by(institution_id=institution.id)
    business_today = datetime.now(BUSINESS_TZ).date()
    tab_counts = {
        "today": base.filter_by(appointment_date=business_today, status="unfulfilled").count(),
        "archive": base.filter_by(status="awaiting_report").count(),
        "all": base.count(),
    }
    view = (request.args.get("view") or "all").strip()
    query = base
    if view == "today":
        query = query.filter_by(appointment_date=business_today, status="unfulfilled")
    elif view == "archive":
        query = query.filter_by(status="awaiting_report")
    status = (request.args.get("status") or "").strip()
    if view == "all" and status: query = query.filter_by(status=status)
    day = parse_date(request.args.get("appointment_date")) if request.args.get("appointment_date") else None
    if day: query = query.filter_by(appointment_date=day)
    page = max(request.args.get("page", 1, type=int) or 1, 1); size = min(max(request.args.get("page_size", 30, type=int) or 30, 1), 100)
    total = query.count(); rows = query.order_by(Appointment.appointment_date.desc(), Appointment.booking_group_id, Appointment.id).offset((page - 1) * size).limit(size).all()
    summary = None
    if day:
        all_day = Appointment.query.filter_by(institution_id=institution.id, appointment_date=day)
        active = all_day.filter(Appointment.status.in_(("unfulfilled", "awaiting_report", "fulfilled"))).count()
        summary = {"appointment_date": day.isoformat(), "capacity": institution.daily_appointment_limit,
                   "booked": active, "remaining": None if institution.daily_appointment_limit is None else max(institution.daily_appointment_limit - active, 0),
                   "attended": all_day.filter(Appointment.status.in_(("awaiting_report", "fulfilled"))).count(),
                   "waitlist_subscriptions": WaitlistSubscription.query.filter_by(institution_id=institution.id, appointment_date=day, status="active").count()}
    return {"items": [item.to_dict(include_user=True) for item in rows], "summary": summary, "tab_counts": tab_counts,
            "pagination": {"page": page, "page_size": size, "total": total, "pages": (total + size - 1) // size}}, 200


@org_bp.post("/appointments/<int:appointment_id>/attend")
@roles_required(ROLE_INSTITUTION_ADMIN)
def attend_appointment(appointment_id):
    institution, error = managed_institution()
    if error: return error
    item = Appointment.query.filter_by(id=appointment_id, institution_id=institution.id).first()
    if item is None: return {"message": "appointment not found"}, 404
    if item.status != "unfulfilled": return {"message": "only unfulfilled appointments can be confirmed"}, 409
    item.status = "awaiting_report"; item.attended_at = datetime.now(timezone.utc)
    db.session.add(AppointmentEvent(appointment_id=item.id, event_type="attended", status_snapshot="awaiting_report",
                                    message="机构确认到检", actor_user_id=g.current_user.id, occurred_at=item.attended_at))
    db.session.commit()
    return {"item": item.to_dict(include_user=True)}, 200


@org_bp.post("/appointments/<int:appointment_id>/invalidate")
@roles_required(ROLE_INSTITUTION_ADMIN)
def invalidate_appointment(appointment_id):
    institution, error = managed_institution()
    if error: return error
    item = Appointment.query.filter_by(id=appointment_id, institution_id=institution.id).first()
    if item is None: return {"message": "appointment not found"}, 404
    if item.status != "unfulfilled": return {"message": "only unfulfilled appointments can be invalidated"}, 409
    item.status = "invalidated"; item.active_date_key = None; item.invalidated_at = datetime.now(timezone.utc)
    db.session.add(AppointmentEvent(appointment_id=item.id, event_type="invalidated", status_snapshot="invalidated",
                                    message="预约已失效", actor_user_id=g.current_user.id, occurred_at=item.invalidated_at))
    from app.booking_v7.routes import _lock_capacity, enqueue_available
    slot = _lock_capacity(institution, item.appointment_date); slot.revision += 1; enqueue_available(institution, item.appointment_date, slot)
    db.session.commit()
    return {"item": item.to_dict(include_user=True)}, 200


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
    scope = (request.args.get("scope") or "branch").strip().lower()
    if scope not in {"branch", "organization"}:
        return {"message": "scope must be branch or organization"}, 400
    if scope == "organization":
        branch_ids = [branch.id for branch in institution.organization.branches]
        query = InstitutionReport.query.filter(
            InstitutionReport.institution_id.in_(branch_ids),
            InstitutionReport.status == "published",
        )
    else:
        query = InstitutionReport.query.filter_by(institution_id=institution.id)
    status = (request.args.get("status") or "").strip()
    if status: query = query.filter_by(status=status)
    source_branch_id = request.args.get("source_branch_id", type=int)
    if source_branch_id:
        allowed = {branch.id for branch in institution.organization.branches}
        if source_branch_id not in allowed:
            return {"message": "source branch is outside this organization"}, 403
        query = query.filter(InstitutionReport.institution_id == source_branch_id)
    subject = (request.args.get("subject") or "").strip()
    if subject:
        query = query.filter(db.or_(
            InstitutionReport.subject_name_snapshot.ilike(f"%{subject}%"),
            InstitutionReport.subject_health_id.ilike(f"%{subject}%"),
        ))
    start = parse_date(request.args.get("start_date")) if request.args.get("start_date") else None
    end = parse_date(request.args.get("end_date")) if request.args.get("end_date") else None
    if start: query = query.filter(InstitutionReport.exam_date >= start)
    if end: query = query.filter(InstitutionReport.exam_date <= end)
    domain_id = request.args.get("domain_id", type=int)
    if domain_id:
        query = query.filter(db.or_(
            InstitutionReport.indicators.any(ReportIndicator.display_domain_id == domain_id),
            InstitutionReport.text_results.any(health_domain_id=domain_id),
            InstitutionReport.assets.any(health_domain_id=domain_id),
        ))
    rows = query.order_by(InstitutionReport.exam_date.desc(), InstitutionReport.id.desc()).all()
    return {"scope": scope, "items": [report_payload(row, institution) for row in rows]}, 200


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
    report, _own_branch, error = readable_report(report_id)
    if error: return error
    institution = g.current_user.managed_institution
    log_cross_branch_access(report, "detail")
    return {"item": report_payload(report, institution, include_indicators=True)}, 200


@org_bp.get("/reports/<int:report_id>/assets/<int:asset_id>/content")
@roles_required(ROLE_INSTITUTION_ADMIN)
def get_report_asset_content(report_id, asset_id):
    report, _own_branch, error = readable_report(report_id)
    if error: return error
    asset = ReportAsset.query.filter_by(id=asset_id, report_id=report.id).first()
    if asset is None:
        return {"message": "asset not found"}, 404
    path = Path(current_app.config["UPLOAD_DIR"]) / asset.storage_key
    if not path.is_file():
        return {"message": "asset content unavailable"}, 404
    log_cross_branch_access(report, "asset")
    return send_file(path, mimetype=asset.mime_type, download_name=asset.title, conditional=True)


@org_bp.put("/reports/<int:report_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_report(report_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked reports are immutable"}, 409
    payload = request.get_json(silent=True) or {}
    if report.appointment_id and any(key in payload for key in ("subject_name", "subject_health_id", "exam_date", "package_id")):
        return {"message": "appointment identity, date and package are immutable"}, 409
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
    try: display_domain_id = admit_indicator(report, definition.id)
    except DomainAdmissionError as exc: return {"message": str(exc), "code": "DOMAIN_NOT_ALLOWED"}, 400
    try: value = normalize_indicator_value(definition, payload.get("value"))
    except IndicatorValueError as exc: return {"message": str(exc)}, 400
    row = ReportIndicator(report_id=report.id, indicator_dict_id=definition.id, value=value,
        is_abnormal=evaluate_is_abnormal(definition, value),
        input_source=payload.get("input_source") if payload.get("input_source") in {"manual", "ocr"} else "manual",
        display_domain_id=display_domain_id, original_name=(payload.get("original_name") or definition.name).strip(),
        original_value=str(payload.get("original_value", payload.get("value"))),
        original_unit=(payload.get("original_unit") or definition.unit), normalized_unit=definition.unit,
        reference_text=(payload.get("reference_text") or "").strip() or None,
        method_snapshot=(payload.get("method") or "").strip() or None,
        abnormal_flag=(payload.get("abnormal_flag") or "").strip() or None,
        mapping_confidence=payload.get("mapping_confidence"), mapping_status="confirmed")
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
    try: display_domain_id = admit_indicator(report, definition.id)
    except DomainAdmissionError as exc: return {"message": str(exc), "code": "DOMAIN_NOT_ALLOWED"}, 400
    try: value = normalize_indicator_value(definition, payload.get("value", row.value))
    except IndicatorValueError as exc: return {"message": str(exc)}, 400
    row.indicator_dict_id = definition.id; row.value = value; row.is_abnormal = evaluate_is_abnormal(definition, value); row.display_domain_id = display_domain_id
    row.original_name = (payload.get("original_name") or row.original_name or definition.name).strip()
    row.original_value = str(payload.get("original_value", payload.get("value", row.original_value or value)))
    row.original_unit = payload.get("original_unit", row.original_unit or definition.unit); row.normalized_unit = definition.unit
    row.reference_text = payload.get("reference_text", row.reference_text); row.method_snapshot = payload.get("method", row.method_snapshot)
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


def _allowed_report_domain(report, raw_domain_id):
    try: domain_id = int(raw_domain_id)
    except (TypeError, ValueError): return None, ({"message": "health_domain_id must be an integer"}, 400)
    domain = db.session.get(HealthDomain, domain_id)
    if not domain or domain_id not in report_allowed_domain_ids(report):
        return None, ({"message": "health domain is outside the appointment package snapshot", "code": "DOMAIN_NOT_ALLOWED"}, 400)
    return domain, None


@org_bp.post("/health-data/<int:report_id>/text-results")
@roles_required(ROLE_INSTITUTION_ADMIN)
def add_text_result(report_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked health data is immutable"}, 409
    payload = request.get_json(silent=True) or {}
    domain, error = _allowed_report_domain(report, payload.get("health_domain_id"))
    if error: return error
    title, body = str(payload.get("title") or "").strip(), str(payload.get("body") or "").strip()
    if not title or not body: return {"message": "title and body are required"}, 400
    row = ReportTextResult(report_id=report.id, health_domain_id=domain.id, title=title, body=body,
        source_snapshot=(payload.get("source") or "机构结论").strip(), sort_order=int(payload.get("sort_order") or 0),
        created_by_user_id=g.current_user.id)
    db.session.add(row); db.session.commit(); return {"item": row.to_dict()}, 201


@org_bp.patch("/health-data/<int:report_id>/text-results/<int:result_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_text_result(report_id, result_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked health data is immutable"}, 409
    row = ReportTextResult.query.filter_by(id=result_id, report_id=report.id).first()
    if not row: return {"message": "text result not found"}, 404
    payload = request.get_json(silent=True) or {}
    if "health_domain_id" in payload:
        domain, error = _allowed_report_domain(report, payload.get("health_domain_id"))
        if error: return error
        row.health_domain_id = domain.id
    for field in ("title", "body", "source"):
        if field in payload:
            value = str(payload.get(field) or "").strip()
            if field in {"title", "body"} and not value: return {"message": f"{field} cannot be blank"}, 400
            setattr(row, "source_snapshot" if field == "source" else field, value or None)
    if "sort_order" in payload: row.sort_order = int(payload.get("sort_order") or 0)
    db.session.commit(); return {"item": row.to_dict()}, 200


@org_bp.delete("/health-data/<int:report_id>/text-results/<int:result_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def delete_text_result(report_id, result_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked health data is immutable"}, 409
    row = ReportTextResult.query.filter_by(id=result_id, report_id=report.id).first()
    if not row: return {"message": "text result not found"}, 404
    db.session.delete(row); db.session.commit(); return {"message": "text result deleted"}, 200


def _asset_metadata(path, extension):
    size = os.path.getsize(path)
    if size <= 0 or size > current_app.config.get("HEALTH_ASSET_MAX_BYTES", 20 * 1024 * 1024):
        raise ValueError("asset size is outside the allowed range")
    if extension == ".pdf":
        import fitz
        with fitz.open(path) as document:
            pages = document.page_count
        if pages < 1 or pages > current_app.config.get("HEALTH_ASSET_MAX_PAGES", 50):
            raise ValueError("PDF page count is outside the allowed range")
        mime, width, height = "application/pdf", None, None
        return mime, width, height, pages, size
    from PIL import Image
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        format_name = image.format; width, height = image.size
    expected = {"JPEG": ("image/jpeg", {".jpg", ".jpeg"}), "PNG": ("image/png", {".png"}), "WEBP": ("image/webp", {".webp"})}
    if format_name not in expected or extension not in expected[format_name][1]:
        raise ValueError("file extension does not match its actual image type")
    if width * height > current_app.config.get("HEALTH_ASSET_MAX_PIXELS", 40_000_000):
        raise ValueError("image pixel count exceeds the limit")
    return expected[format_name][0], width, height, None, size


@org_bp.post("/health-data/<int:report_id>/assets")
@roles_required(ROLE_INSTITUTION_ADMIN)
def add_asset(report_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked health data is immutable"}, 409
    upload = request.files.get("file")
    if not upload or not upload.filename: return {"message": "file is required"}, 400
    extension = Path(upload.filename).suffix.lower()
    if extension not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}: return {"message": "unsupported file type"}, 400
    domain, error = _allowed_report_domain(report, request.form.get("health_domain_id"))
    if error: return error
    title = str(request.form.get("title") or Path(upload.filename).stem).strip()
    modality = str(request.form.get("modality") or ("pdf" if extension == ".pdf" else "image")).strip()
    storage = get_storage_backend(current_app.config); saved = storage.save(upload, subdir="health-assets")
    try:
        mime, width, height, pages, size = _asset_metadata(saved["abs_path"], extension)
        digest = hashlib.sha256(Path(saved["abs_path"]).read_bytes()).hexdigest()
        row = ReportAsset(report_id=report.id, health_domain_id=domain.id, modality=modality,
            title=title, storage_key=saved["key"], mime_type=mime, byte_size=size,
            width=width, height=height, page_count=pages, sha256=digest,
            annotation_text=str(request.form.get("annotation") or "").strip() or None,
            sort_order=int(request.form.get("sort_order") or 0), uploaded_by_user_id=g.current_user.id)
        db.session.add(row); db.session.commit()
    except Exception as exc:
        db.session.rollback(); storage.delete(saved["key"])
        if isinstance(exc, ValueError): return {"message": str(exc)}, 400
        raise
    return {"item": row.to_dict(f"hd-i-{report.id:x}")}, 201


@org_bp.patch("/health-data/<int:report_id>/assets/<int:asset_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_asset(report_id, asset_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked health data is immutable"}, 409
    row = ReportAsset.query.filter_by(id=asset_id, report_id=report.id).first()
    if not row: return {"message": "asset not found"}, 404
    payload = request.get_json(silent=True) or {}
    if "health_domain_id" in payload:
        domain, error = _allowed_report_domain(report, payload.get("health_domain_id"))
        if error: return error
        row.health_domain_id = domain.id
    for field, attr in (("title", "title"), ("modality", "modality"), ("annotation", "annotation_text")):
        if field in payload:
            value = str(payload.get(field) or "").strip()
            if field in {"title", "modality"} and not value: return {"message": f"{field} cannot be blank"}, 400
            setattr(row, attr, value or None)
    if "sort_order" in payload: row.sort_order = int(payload.get("sort_order") or 0)
    db.session.commit(); return {"item": row.to_dict(f"hd-i-{report.id:x}")}, 200


@org_bp.delete("/health-data/<int:report_id>/assets/<int:asset_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def delete_asset(report_id, asset_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "locked health data is immutable"}, 409
    row = ReportAsset.query.filter_by(id=asset_id, report_id=report.id).first()
    if not row: return {"message": "asset not found"}, 404
    key = row.storage_key; db.session.delete(row); db.session.commit()
    get_storage_backend(current_app.config).delete(key)
    return {"message": "asset deleted"}, 200


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
        admitted_candidates, excluded = [], []
        for candidate in mapping.get("candidate_mappings", []):
            if candidate.get("requires_review"): continue
            definition = db.session.get(IndicatorDict, candidate["indicator_dict_id"])
            try: display_domain_id = admit_indicator(report, definition.id)
            except DomainAdmissionError:
                excluded.append({"field": candidate.get("raw_name") or definition.name, "reason": "outside_package_domain"}); continue
            try: value = normalize_ocr_indicator_value(definition, candidate["value"])
            except IndicatorValueError: continue
            report.indicators.append(ReportIndicator(indicator_dict_id=definition.id, value=value,
                is_abnormal=evaluate_is_abnormal(definition, value), input_source="ocr",
                display_domain_id=display_domain_id, original_name=candidate.get("raw_name") or definition.name,
                original_value=str(candidate.get("value")), original_unit=candidate.get("unit") or definition.unit,
                normalized_unit=definition.unit, mapping_confidence=candidate.get("score"), mapping_status="confirmed"))
            admitted_candidates.append(candidate)
        report.ocr_diagnostics = {**(report.ocr_diagnostics or {}), "excluded": excluded, "excluded_count": len(excluded)}
        db.session.commit()
    except Exception:
        db.session.rollback(); storage.delete(saved["key"]); raise
    return {"item": report.to_dict(include_indicators=True), "ocr": {"candidate_mappings": admitted_candidates, "excluded": excluded, "diagnostics": report.ocr_diagnostics}}, 201


@org_bp.post("/reports/<int:report_id>/lock")
@roles_required(ROLE_INSTITUTION_ADMIN)
def lock_report(report_id):
    report, error = scoped_report(report_id)
    if error: return error
    if report.status != "draft": return {"message": "only draft reports can be locked"}, 409
    if not report.indicators and not report.text_results and not report.assets: return {"message": "at least one indicator, text result or asset is required"}, 400
    try: validate_report_domains(report)
    except DomainAdmissionError as exc: return {"message": str(exc), "code": "DOMAIN_NOT_ALLOWED"}, 400
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
    try:
        submit_report(report)
        if report.appointment is not None:
            if report.appointment.status != "awaiting_report":
                raise ValueError("appointment is not awaiting a report")
            report.appointment.status = "fulfilled"
            report.appointment.fulfilled_at = datetime.now(timezone.utc)
            db.session.add(AppointmentEvent(appointment_id=report.appointment.id, event_type="archived",
                status_snapshot="fulfilled", message="健康数据已归档", actor_user_id=g.current_user.id,
                occurred_at=report.appointment.fulfilled_at))
        db.session.commit()
    except ValueError as exc: db.session.rollback(); return {"message": str(exc)}, 409
    except IntegrityError: db.session.rollback(); return {"message": "report publishing conflict; reload and retry"}, 409
    db.session.refresh(report)
    return {"item": report.to_dict(include_indicators=True), "match_result": "matched"}, 200
