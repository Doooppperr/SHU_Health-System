from __future__ import annotations

import json
import os
import hashlib
from io import BytesIO
from pathlib import Path
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app, g, request, send_file
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models import (
    Appointment, AppointmentComplaint, AppointmentEvent, ComplaintEvent,
    ComplaintMessage,
    HealthDomain, IndicatorDict, Institution, InstitutionReport, Package,
    PackageChangeRequest, ReportAsset,
    ReportAccessLog, ReportIndicator, ReportTextResult, WaitlistSubscription,
    User, PackageVersionAssetRequirement, ReportAssetType,
)
from app.org import org_bp
from app.services import get_ocr_provider, get_storage_backend
from app.services.indicator_values import (
    IndicatorValueError,
    evaluate_result_status,
    normalize_indicator_value,
    normalize_ocr_indicator_value,
    validate_indicator_plausibility,
)
from app.services.notifications import enqueue_user_notification
from app.services.password_challenges import (
    increment_user_security_epochs,
    revoke_account_security_artifacts,
)
from app.services.platform_contact import PLATFORM_CONTACT
from app.services.institution_management import (
    ManagementValidationError, apply_institution_payload, apply_package_payload,
    delete_institution_image, image_payload, institution_payload,
    reorder_institution_images, save_institution_image,
)
from app.services.ocr import mapping_service
from app.services.permissions import ROLE_INSTITUTION_ADMIN, roles_required
from app.services.package_reviews import create_change_request
from app.services.record_files import delete_report_urls
from app.services.reports import find_subject_user
from app.services.report_conclusions import missing_conclusion_domains
from app.services.audience_insights import get_audience_insight
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


def scoped_report(report_id, *, for_update=False):
    institution, error = managed_institution()
    if error:
        return None, error
    query = InstitutionReport.query.filter_by(
        id=report_id,
        institution_id=institution.id,
    )
    if for_update:
        # openGauss/PostgreSQL serialize reviewers at the report row. SQLite
        # ignores FOR UPDATE, so the guarded state transition below remains the
        # authoritative cross-database concurrency check.
        query = query.with_for_update()
    report = query.first()
    return (report, None) if report else (None, ({"message": "report not found"}, 404))


def scoped_editable_report(report_id):
    """Lock/claim the parent report before any mutable child is changed.

    ``FOR UPDATE`` serializes openGauss writers. The guarded no-op UPDATE also
    acquires SQLite's write lock, so a concurrent publish either observes this
    edit first or wins the status transition and makes this edit fail.
    """
    report, error = scoped_report(report_id, for_update=True)
    if error:
        return None, error
    if report.status not in {"draft", "pending_review"}:
        return None, (
            {
                "message": "published reports are immutable",
                "code": "REPORT_STATE_CONFLICT",
            },
            409,
        )
    try:
        claimed = _claim_editable_report_cas(report.id)
    except OperationalError:
        db.session.rollback()
        return None, (
            {
                "message": "report is being updated; reload and retry",
                "code": "REPORT_STATE_CONFLICT",
            },
            409,
        )
    if claimed != 1:
        db.session.rollback()
        return None, (
            {
                "message": "report state changed; reload and retry",
                "code": "REPORT_STATE_CONFLICT",
            },
            409,
        )
    return report, None


def _claim_editable_report_cas(report_id):
    """Claim an editable parent before touching any report child row."""
    return InstitutionReport.query.filter(
        InstitutionReport.id == report_id,
        InstitutionReport.status.in_(("draft", "pending_review")),
    ).update(
        {InstitutionReport.status: InstitutionReport.status},
        synchronize_session=False,
    )


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
        "can_edit": own_branch and report.status in {"draft", "pending_review"},
        "subject_display_name": report.owner.real_name if report.owner else report.subject_name_snapshot,
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
    db.session.add(AppointmentEvent(
        appointment_id=appointment.id,
        event_type="report_uploaded",
        status_snapshot=appointment.status,
        message="体检报告已上传，等待上传医生确认",
        actor_user_id=g.current_user.id,
        occurred_at=datetime.now(timezone.utc),
    ))
    return report, None


@org_bp.get("/dashboard")
@roles_required(ROLE_INSTITUTION_ADMIN)
def dashboard():
    institution, error = managed_institution()
    if error:
        return error
    counts = {
        status: InstitutionReport.query.filter_by(
            institution_id=institution.id,
            status=status,
        ).count()
        for status in ("draft", "pending_review", "published")
    }
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
    from app.services.finance import institution_finance_summary, run_due_finance_tasks
    run_due_finance_tasks()
    db.session.commit()
    finance_summary = institution_finance_summary(institution)
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
        "finance": finance_summary,
    }}, 200


@org_bp.get("/finance/summary")
@roles_required(ROLE_INSTITUTION_ADMIN)
def finance_summary():
    institution, error = managed_institution()
    if error:
        return error
    from app.services.finance import institution_finance_summary, run_due_finance_tasks
    run_due_finance_tasks()
    db.session.commit()
    return {"summary": institution_finance_summary(institution)}, 200


@org_bp.get("/finance/orders")
@roles_required(ROLE_INSTITUTION_ADMIN)
def finance_orders():
    institution, error = managed_institution()
    if error:
        return error
    from app.models import PaymentOrderItem
    from app.services.finance import run_due_finance_tasks
    run_due_finance_tasks()
    db.session.commit()
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 15, type=int) or 15, 1), 100)
    query = PaymentOrderItem.query.filter_by(institution_id=institution.id)
    status = str(request.args.get("status") or "").strip()
    if status:
        query = query.filter_by(fund_status=status)
    total = query.count()
    rows = query.order_by(PaymentOrderItem.created_at.desc(), PaymentOrderItem.id.desc()).offset(
        (page - 1) * size
    ).limit(size).all()
    return {
        "items": [{**row.to_dict(), "order_no": row.order.order_no, "order_status": row.order.status} for row in rows],
        "pagination": {"page": page, "page_size": size, "total": total, "pages": (total + size - 1) // size},
    }, 200


@org_bp.post("/finance/orders/<int:item_id>/refund")
@roles_required(ROLE_INSTITUTION_ADMIN)
def refund_finance_order(item_id):
    institution, error = managed_institution()
    if error:
        return error
    from app.models import PaymentOrderItem
    from app.services.finance import refund_item
    item = PaymentOrderItem.query.filter_by(
        id=item_id,
        institution_id=institution.id,
    ).with_for_update().first()
    if item is None:
        return {"message": "未找到该到账订单"}, 404
    if item.fund_status not in {"settled", "refund_required"}:
        return {"message": "当前订单不能由机构退款"}, 409
    complaint = item.refund_case.complaint if item.refund_case else None
    refund_item(item, actor_user=g.current_user, complaint=complaint, reason="institution_refund")
    if complaint and complaint.status != "resolved":
        complaint.status = "resolved"
        complaint.resolved_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"item": item.to_dict(), "message": "退款已完成并原路退回"}, 200


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


@org_bp.get("/audience-insights")
@roles_required(ROLE_INSTITUTION_ADMIN)
def audience_insights():
    institution, error = managed_institution()
    if error:
        return error
    scope = str(request.args.get("scope") or "branch").strip()
    if scope not in {"branch", "organization"}:
        return {"message": "scope must be branch or organization"}, 400
    try:
        period_days = int(request.args.get("period_days", 365))
    except (TypeError, ValueError):
        return {"message": "period_days must be an integer"}, 400
    if period_days not in {0, 30, 90, 365}:
        return {"message": "period_days must be one of 0, 30, 90 or 365"}, 400
    item, cache_hit = get_audience_insight(
        institution,
        scope=scope,
        period_days=period_days,
    )
    serialized = item.to_dict()
    return {
        "item": serialized,
        "aggregate": serialized["aggregate"],
        "ai": {
            "analysis_text": serialized["analysis_text"],
            "source": serialized["source"],
            "model": serialized["model"],
            "generated_at": serialized["generated_at"],
            "cache_hit": cache_hit,
        },
    }, 200


def _account_deactivation_summary(institution):
    business_today = datetime.now(BUSINESS_TZ).date()
    arrived_unfinished_reports = Appointment.query.filter_by(
        institution_id=institution.id,
        status="awaiting_report",
    ).count()
    draft_or_pending_reports = InstitutionReport.query.filter(
        InstitutionReport.institution_id == institution.id,
        InstitutionReport.status.in_(("draft", "pending_review")),
    ).count()
    future_effective_appointments = Appointment.query.filter(
        Appointment.institution_id == institution.id,
        Appointment.status == "unfulfilled",
        Appointment.appointment_date >= business_today,
    ).count()
    unresolved_complaints = AppointmentComplaint.query.filter(
        AppointmentComplaint.institution_id == institution.id,
        AppointmentComplaint.status != "resolved",
    ).count()
    # An arrived appointment without any report row is an explicit upload task
    # that would otherwise be hidden by a report-status-only check.  Past
    # appointments that were never attended/cancelled are also operational work
    # requiring reconciliation before the branch can disappear.
    missing_report_uploads = Appointment.query.filter(
        Appointment.institution_id == institution.id,
        Appointment.status == "awaiting_report",
        ~Appointment.report.has(),
    ).count()
    overdue_unreconciled_appointments = Appointment.query.filter(
        Appointment.institution_id == institution.id,
        Appointment.status == "unfulfilled",
        Appointment.appointment_date < business_today,
    ).count()
    other_upload_tasks = (
        missing_report_uploads + overdue_unreconciled_appointments
    )
    active_waitlist_subscriptions = WaitlistSubscription.query.filter_by(
        institution_id=institution.id,
        status="active",
    ).count()
    canonical_blockers = {
        "future_effective_appointments": future_effective_appointments,
        "arrived_unfinished_reports": arrived_unfinished_reports,
        "draft_or_pending_reports": draft_or_pending_reports,
        "unresolved_complaints": unresolved_complaints,
        "other_upload_tasks": other_upload_tasks,
    }
    pending_report_tasks = max(
        arrived_unfinished_reports,
        draft_or_pending_reports,
    )
    return {
        **canonical_blockers,
        "active_waitlist_subscriptions": active_waitlist_subscriptions,
        # Transitional aliases keep older clients readable while the five
        # canonical blocker fields above are the deactivation contract.
        "pending_report_tasks": pending_report_tasks,
        "pending_appointment_uploads": arrived_unfinished_reports,
        "unfinished_reports": draft_or_pending_reports,
        "upcoming_appointments": future_effective_appointments,
        "can_deactivate": not any(canonical_blockers.values()),
    }


@org_bp.get("/account/deactivation-check")
@roles_required(ROLE_INSTITUTION_ADMIN)
def account_deactivation_check():
    institution, error = managed_institution()
    if error:
        return error
    return {"item": _account_deactivation_summary(institution)}, 200


@org_bp.post("/account/deactivate")
@roles_required(ROLE_INSTITUTION_ADMIN)
def deactivate_own_account():
    institution, error = managed_institution()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") is not True:
        return {"message": "请确认注销机构账号"}, 400
    if not g.current_user.check_password(str(payload.get("current_password") or "")):
        return {"message": "当前密码不正确", "code": "CURRENT_PASSWORD_INCORRECT"}, 400
    summary = _account_deactivation_summary(institution)
    if not summary["can_deactivate"]:
        return {
            "message": (
                "请先完成全部报告上传与复核任务，并处理有效预约、遗留任务和未解决投诉后再注销"
            ),
            "code": "INSTITUTION_DEACTIVATION_BLOCKED",
            "blockers": summary,
        }, 409
    now = datetime.now(timezone.utc)
    institution.is_active = False
    institution.account_deactivated_at = now
    g.current_user.is_active = False
    versions = increment_user_security_epochs(g.current_user.id)
    deactivation_version = versions[g.current_user.id]["token_version"]
    revoke_account_security_artifacts(g.current_user.id, revoked_at=now)
    invalidated_waitlists = WaitlistSubscription.query.filter_by(
        institution_id=institution.id,
        status="active",
    ).all()
    institution_display_name = (
        institution.organization.name
        if institution.organization is not None
        else institution.name
    )
    for subscription in invalidated_waitlists:
        subscription.status = "invalid"
        subscription.closed_at = now
        subscriber = db.session.get(User, subscription.subscriber_user_id)
        if subscriber is None:
            continue
        appointment_date = subscription.appointment_date.isoformat()
        body = (
            f"您订阅的 {institution_display_name}·{institution.branch_name}"
            f"（{appointment_date}）候补提醒已因分院注销而关闭，"
            "可前往机构列表选择其他分院。"
        )
        enqueue_user_notification(
            subscriber,
            event_type="waitlist_institution_deactivated",
            idempotency_key=(
                f"institution:{institution.id}:deactivated:"
                f"v{deactivation_version}:waitlist:{subscription.id}"
            ),
            title="候补订阅已关闭",
            body=body,
            action_url="/institutions",
            payload={
                "institution_id": institution.id,
                "waitlist_subscription_id": subscription.id,
                "appointment_date": appointment_date,
                "reason": "institution_deactivated",
            },
            email_payload={
                "institution": institution_display_name,
                "branch": institution.branch_name,
                "appointment_date": appointment_date,
                "message": body,
                "platform_contact": PLATFORM_CONTACT,
                "login_url": "/institutions",
            },
        )
    if institution.invite and institution.invite.status == "active":
        institution.invite.status = "superseded"
    db.session.commit()
    return {
        "message": "机构账号与分院已注销，历史业务数据继续保留",
        "deactivated_at": now.isoformat(),
        "invalidated_waitlist_subscriptions": len(invalidated_waitlists),
    }, 200


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
    query = Package.query.filter_by(institution_id=item.id)
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 15, type=int) or 15, 1), 100)
    total = query.count()
    rows = []
    for package in query.order_by(Package.id).offset((page - 1) * size).limit(size).all():
        payload = package.to_dict()
        pending = PackageChangeRequest.query.filter_by(package_id=package.id, status="pending").first()
        payload["pending_request"] = pending.to_dict() if pending else None
        rows.append(payload)
    return {
        "items": rows,
        "summary": {
            "total": total,
            "active": query.filter_by(is_active=True).count(),
            "pending": PackageChangeRequest.query.filter_by(
                institution_id=item.id,
                status="pending",
            ).count(),
        },
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200


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
    query = PackageChangeRequest.query.filter_by(institution_id=institution.id).order_by(
        PackageChangeRequest.requested_at.desc(), PackageChangeRequest.id.desc()
    )
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 15, type=int) or 15, 1), 100)
    total = query.count()
    rows = query.offset((page - 1) * size).limit(size).all()
    return {
        "items": [item.to_dict() for item in rows],
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200


@org_bp.post("/package-change-requests/<int:request_id>/withdraw")
@roles_required(ROLE_INSTITUTION_ADMIN)
def withdraw_package_change_request(request_id):
    institution, error = managed_institution()
    if error: return error
    item = PackageChangeRequest.query.filter_by(id=request_id, institution_id=institution.id).first()
    if item is None: return {"message": "review request not found"}, 404
    if item.status != "pending": return {"message": "only pending requests can be withdrawn"}, 409
    withdrawn_at = datetime.now(timezone.utc)
    if not _withdraw_package_change_request_cas(
        request_id=item.id,
        institution_id=institution.id,
        withdrawn_at=withdrawn_at,
    ):
        db.session.rollback()
        return {"message": "only pending requests can be withdrawn"}, 409
    db.session.commit()
    return {"item": item.to_dict()}, 200


def _withdraw_package_change_request_cas(*, request_id, institution_id, withdrawn_at):
    changed = PackageChangeRequest.query.filter(
        PackageChangeRequest.id == request_id,
        PackageChangeRequest.institution_id == institution_id,
        PackageChangeRequest.status == "pending",
    ).update(
        {
            PackageChangeRequest.status: "withdrawn",
            PackageChangeRequest.withdrawn_at: withdrawn_at,
        },
        synchronize_session=False,
    )
    return changed == 1


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
    subject = (request.args.get("subject") or "").strip()
    if subject:
        pattern = f"%{subject}%"
        query = query.filter(db.or_(
            Appointment.user_name_snapshot.ilike(pattern),
            Appointment.user_health_id_snapshot.ilike(pattern),
        ))
    page = max(request.args.get("page", 1, type=int) or 1, 1); size = min(max(request.args.get("page_size", 15, type=int) or 15, 1), 100)
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


@org_bp.get("/complaints")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_institution_complaints():
    institution, error = managed_institution()
    if error:
        return error
    query = AppointmentComplaint.query.filter_by(institution_id=institution.id)
    status = str(request.args.get("status") or "").strip()
    if status:
        query = query.filter_by(status=status)
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 15, type=int) or 15, 1), 100)
    total = query.count()
    rows = query.order_by(
        AppointmentComplaint.updated_at.desc(),
        AppointmentComplaint.id.desc(),
    ).offset((page - 1) * size).limit(size).all()
    return {
        "items": [row.to_dict() for row in rows],
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200


@org_bp.post("/complaints/<int:complaint_id>/reply")
@roles_required(ROLE_INSTITUTION_ADMIN)
def reply_to_complaint(complaint_id):
    institution, error = managed_institution()
    if error:
        return error
    item = AppointmentComplaint.query.filter_by(
        id=complaint_id,
        institution_id=institution.id,
    ).first()
    if item is None:
        return {"message": "未找到该投诉记录"}, 404
    if item.status != "institution_pending":
        return {
            "message": "只有待机构处理的投诉可以回复",
            "code": "COMPLAINT_STATE_CONFLICT",
        }, 409
    content = str((request.get_json(silent=True) or {}).get("content") or "").strip()
    if not content:
        return {"message": "请填写机构处理回复"}, 400
    if len(content) > 2000:
        return {"message": "机构处理回复不能超过2000个字符"}, 400
    now = datetime.now(timezone.utc)
    if not _reply_to_complaint_cas(
        complaint_id=item.id,
        institution_id=institution.id,
        replier_user_id=g.current_user.id,
        content=content,
        replied_at=now,
    ):
        db.session.rollback()
        return {
            "message": "投诉状态已变化，请刷新后重试",
            "code": "COMPLAINT_STATE_CONFLICT",
        }, 409
    db.session.add(ComplaintEvent(
        complaint_id=item.id,
        event_type="institution_replied",
        actor_user_id=g.current_user.id,
        actor_role=g.current_user.role,
        content=content,
        created_at=now,
    ))
    db.session.add(ComplaintMessage(
        complaint_id=item.id,
        sender_user_id=g.current_user.id,
        sender_role=g.current_user.role,
        content=content,
        created_at=now,
    ))
    if item.complainant is not None:
        enqueue_user_notification(
            item.complainant,
            event_type="complaint_institution_replied",
            idempotency_key=f"complaint:{item.id}:institution-replied",
            title="机构已回复您的投诉",
            body="请查看机构处理结果，并确认解决或申请平台介入。",
            action_url=f"/appointments?complaint_id={item.id}",
            payload={"complaint_id": item.id},
        )
    db.session.commit()
    return {"item": item.to_dict(), "message": "处理回复已提交，等待用户确认"}, 200


@org_bp.post("/complaints/<int:complaint_id>/approve-refund")
@roles_required(ROLE_INSTITUTION_ADMIN)
def approve_complaint_refund(complaint_id):
    institution, error = managed_institution()
    if error:
        return error
    item = AppointmentComplaint.query.filter_by(
        id=complaint_id,
        institution_id=institution.id,
    ).with_for_update().first()
    if item is None:
        return {"message": "未找到该投诉与退款记录"}, 404
    if item.status != "institution_pending" or item.refund_case is None:
        return {"message": "当前案件不能由机构直接退款", "code": "COMPLAINT_STATE_CONFLICT"}, 409
    from app.services.finance import refund_item
    now = datetime.now(timezone.utc)
    if not refund_item(
        item.refund_case.payment_item,
        actor_user=g.current_user,
        complaint=item,
        now=now,
        reason="institution_approved",
    ):
        return {"message": "当前订单资金状态不能退款"}, 409
    item.status = "resolved"
    item.resolved_at = now
    item.updated_at = now
    item.institution_reply = "机构已同意全额退款"
    item.institution_replied_by_user_id = g.current_user.id
    item.institution_replied_at = now
    db.session.add(ComplaintEvent(
        complaint_id=item.id,
        event_type="institution_replied",
        actor_user_id=g.current_user.id,
        actor_role=g.current_user.role,
        content="机构同意全额退款，退款已原路退回",
        created_at=now,
    ))
    db.session.add(ComplaintMessage(
        complaint_id=item.id,
        sender_user_id=g.current_user.id,
        sender_role=g.current_user.role,
        content="机构已同意全额退款",
        created_at=now,
    ))
    db.session.commit()
    return {"item": item.to_dict(), "message": "退款已完成并原路退回"}, 200


def _reply_to_complaint_cas(
    *,
    complaint_id,
    institution_id,
    replier_user_id,
    content,
    replied_at,
):
    changed = AppointmentComplaint.query.filter(
        AppointmentComplaint.id == complaint_id,
        AppointmentComplaint.institution_id == institution_id,
        AppointmentComplaint.status == "institution_pending",
    ).update(
        {
            AppointmentComplaint.status: "user_confirmation",
            AppointmentComplaint.institution_reply: content,
            AppointmentComplaint.institution_replied_by_user_id: replier_user_id,
            AppointmentComplaint.institution_replied_at: replied_at,
            AppointmentComplaint.updated_at: replied_at,
        },
        synchronize_session=False,
    )
    return changed == 1


@org_bp.post("/appointments/<int:appointment_id>/attend")
@roles_required(ROLE_INSTITUTION_ADMIN)
def attend_appointment(appointment_id):
    institution, error = managed_institution()
    if error: return error
    item = Appointment.query.filter_by(id=appointment_id, institution_id=institution.id).first()
    if item is None: return {"message": "appointment not found"}, 404
    if item.status != "unfulfilled": return {"message": "only unfulfilled appointments can be confirmed"}, 409
    attended_at = datetime.now(timezone.utc)
    if not _attend_appointment_cas(item.id, attended_at):
        db.session.rollback()
        return {
            "message": "appointment state changed; reload and retry",
            "code": "APPOINTMENT_STATE_CONFLICT",
        }, 409
    db.session.add(AppointmentEvent(appointment_id=item.id, event_type="attended", status_snapshot="awaiting_report",
                                    message="机构确认到检", actor_user_id=g.current_user.id, occurred_at=attended_at))
    db.session.commit()
    return {"item": item.to_dict(include_user=True)}, 200


def _attend_appointment_cas(appointment_id, attended_at):
    try:
        result = db.session.execute(
            update(Appointment)
            .where(
                Appointment.id == appointment_id,
                Appointment.status == "unfulfilled",
            )
            .values(status="awaiting_report", attended_at=attended_at)
            .execution_options(synchronize_session=False)
        )
    except OperationalError:
        db.session.rollback()
        return False
    return result.rowcount == 1


def _close_appointment(item, institution, *, reason_type, reason_code, reason_text):
    now = datetime.now(timezone.utc)
    try:
        updated = _close_appointment_cas(
            item.id,
            closed_at=now,
            reason_type=reason_type,
            reason_code=reason_code,
            reason_text=reason_text,
        )
    except OperationalError:
        db.session.rollback()
        return False
    if updated != 1:
        db.session.rollback()
        return False
    message = "受检者未到检" if reason_type == "no_show" else f"机构原因取消：{reason_text}"
    db.session.add(AppointmentEvent(
        appointment_id=item.id,
        event_type=reason_type,
        status_snapshot=reason_type,
        message=message,
        actor_user_id=g.current_user.id,
        occurred_at=now,
    ))
    from app.booking_v7.routes import _lock_capacity, enqueue_available
    slot = _lock_capacity(institution, item.appointment_date)
    slot.revision += 1
    enqueue_available(institution, item.appointment_date, slot)

    if reason_type == "institution_cancelled":
        from app.models import PaymentOrderItem
        from app.services.finance import refund_item
        payment_item = PaymentOrderItem.query.filter_by(appointment_id=item.id).first()
        if payment_item is not None:
            refund_item(payment_item, actor_user=g.current_user, reason="institution_cancellation")
        alternatives = [
            {
                "id": branch.id,
                "name": branch.organization.name if branch.organization else branch.name,
                "branch_name": branch.branch_name,
                "address": branch.address,
                "consult_phone": branch.consult_phone,
            }
            for branch in institution.organization.branches
            if branch.id != institution.id and branch.is_active
        ]
        notified_ids = {item.user_id}
        if item.booked_by_user_id:
            notified_ids.add(item.booked_by_user_id)
        for user_id in notified_ids:
            user = db.session.get(User, user_id)
            if user is None:
                continue
            branch_text = "；".join(
                f"{row['name']}·{row['branch_name']}，{row['address']}，电话{row['consult_phone'] or '请在平台查看'}"
                for row in alternatives
            )
            solution = branch_text or (
                f"暂无可用兄弟分院，请联系本院"
                f"{institution.consult_phone or '平台客服'}或平台 {PLATFORM_CONTACT['phone']}"
            )
            email_payload = {
                "recipient_name": user.real_name or "用户",
                "institution": institution.name,
                "branch": institution.branch_name,
                "appointment_date": item.appointment_date.isoformat(),
                "package": item.package_name_snapshot,
                "reason": reason_text,
                "alternatives": alternatives,
                "institution_phone": institution.consult_phone,
                "support_phone": PLATFORM_CONTACT["phone"],
                "login_url": "/appointments",
            }
            enqueue_user_notification(
                user,
                event_type="appointment_institution_cancelled",
                idempotency_key=f"appointment:{item.id}:institution-cancelled:user:{user.id}",
                title="很抱歉，机构取消了本次预约",
                body=f"{institution.name}·{institution.branch_name}因“{reason_text}”无法按期提供体检。可选方案：{solution}",
                action_url="/appointments",
                payload={
                    "appointment_id": item.id,
                    "reason": reason_text,
                    "alternatives": alternatives,
                },
                email_payload=email_payload,
            )
    return True


def _close_appointment_cas(
    appointment_id,
    *,
    closed_at,
    reason_type,
    reason_code,
    reason_text,
):
    result = db.session.execute(
        update(Appointment)
        .where(
            Appointment.id == appointment_id,
            Appointment.status == "unfulfilled",
        )
        .values(
            status=reason_type,
            active_date_key=None,
            invalidated_at=closed_at,
            termination_party=(
                "subject" if reason_type == "no_show" else "institution"
            ),
            termination_reason_code=reason_code,
            termination_reason_text=reason_text or None,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


@org_bp.post("/appointments/<int:appointment_id>/close")
@roles_required(ROLE_INSTITUTION_ADMIN)
def close_appointment(appointment_id):
    institution, error = managed_institution()
    if error: return error
    item = Appointment.query.filter_by(id=appointment_id, institution_id=institution.id).first()
    if item is None: return {"message": "appointment not found"}, 404
    if item.status != "unfulfilled": return {"message": "只有待到检预约可以结束"}, 409
    payload = request.get_json(silent=True) or {}
    reason_type = (payload.get("reason_type") or "").strip()
    if reason_type not in {"no_show", "institution_cancelled"}:
        return {"message": "请选择未到检或机构原因取消"}, 400
    reason_text = (payload.get("reason_text") or "").strip()
    reason_code = (payload.get("reason_code") or "").strip()
    if reason_type == "institution_cancelled":
        allowed_codes = {"equipment_failure", "staffing_shortage", "facility_issue", "emergency", "other"}
        if reason_code not in allowed_codes:
            return {"message": "请选择有效的机构取消原因"}, 400
        if len(reason_text) < 5 or len(reason_text) > 500:
            return {"message": "机构取消说明应为 5 至 500 个字符"}, 400
    else:
        reason_code = "no_show"
        reason_text = reason_text[:500]
    closed = _close_appointment(
        item,
        institution,
        reason_type=reason_type,
        reason_code=reason_code,
        reason_text=reason_text,
    )
    if not closed:
        return {
            "message": "appointment state changed; reload and retry",
            "code": "APPOINTMENT_STATE_CONFLICT",
        }, 409
    db.session.commit()
    return {"item": item.to_dict(include_user=True)}, 200


@org_bp.post("/appointments/<int:appointment_id>/invalidate")
@roles_required(ROLE_INSTITUTION_ADMIN)
def invalidate_appointment(appointment_id):
    institution, error = managed_institution()
    if error: return error
    item = Appointment.query.filter_by(id=appointment_id, institution_id=institution.id).first()
    if item is None: return {"message": "appointment not found"}, 404
    if item.status != "unfulfilled": return {"message": "只有待到检预约可以标记未到检"}, 409
    closed = _close_appointment(
        item,
        institution,
        reason_type="no_show",
        reason_code="no_show",
        reason_text="",
    )
    if not closed:
        return {
            "message": "appointment state changed; reload and retry",
            "code": "APPOINTMENT_STATE_CONFLICT",
        }, 409
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
        access = (request.args.get("access") or "").strip().lower()
        if access not in {"", "cross_branch"}:
            return {"message": "档案访问范围不正确"}, 400
        if access == "cross_branch":
            query = query.filter(InstitutionReport.institution_id != institution.id)
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
    total = query.count()
    subject = (request.args.get("subject") or "").strip()
    if subject:
        query = query.filter(db.or_(
            InstitutionReport.subject_name_snapshot.ilike(f"%{subject}%"),
            InstitutionReport.subject_health_id.ilike(f"%{subject}%"),
            InstitutionReport.owner.has(User.username.ilike(f"%{subject}%")),
        ))
    start_raw = request.args.get("start_date")
    end_raw = request.args.get("end_date")
    start = parse_date(start_raw) if start_raw else None
    end = parse_date(end_raw) if end_raw else None
    if start_raw and start is None:
        return {"message": "体检开始日期格式不正确"}, 400
    if end_raw and end is None:
        return {"message": "体检结束日期格式不正确"}, 400
    if start and end and start > end:
        return {"message": "体检开始日期不能晚于结束日期"}, 400
    if start: query = query.filter(InstitutionReport.exam_date >= start)
    if end: query = query.filter(InstitutionReport.exam_date <= end)
    domain_id = request.args.get("domain_id", type=int)
    if domain_id:
        query = query.filter(db.or_(
            InstitutionReport.indicators.any(ReportIndicator.display_domain_id == domain_id),
            InstitutionReport.text_results.any(health_domain_id=domain_id),
            InstitutionReport.assets.any(health_domain_id=domain_id),
        ))
    filtered_total = query.count()
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 15, type=int) or 15, 1), 100)
    rows = query.order_by(InstitutionReport.exam_date.desc(), InstitutionReport.id.desc()).offset((page - 1) * size).limit(size).all()
    return {
        "scope": scope,
        "total": total,
        "filtered_total": filtered_total,
        "items": [report_payload(row, institution) for row in rows],
        "pagination": {
            "page": page,
            "page_size": size,
            "total": filtered_total,
            "pages": (filtered_total + size - 1) // size,
        },
    }, 200


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
    report, error = scoped_editable_report(report_id)
    if error: return error
    if report.status not in {"draft", "pending_review"}:
        return {"message": "published reports are immutable"}, 409
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
    report, error = scoped_editable_report(report_id)
    if error: return error
    if report.status not in {"draft", "pending_review"}:
        return {"message": "published reports are immutable"}, 409
    payload = request.get_json(silent=True) or {}
    definition = db.session.get(IndicatorDict, payload.get("indicator_dict_id"))
    if not definition: return {"message": "indicator not found"}, 404
    try: display_domain_id = admit_indicator(report, definition.id)
    except DomainAdmissionError as exc: return {"message": str(exc), "code": "DOMAIN_NOT_ALLOWED"}, 400
    try: value = normalize_indicator_value(definition, payload.get("value"))
    except IndicatorValueError as exc: return {"message": str(exc)}, 400
    try: validate_indicator_plausibility(definition, value)
    except IndicatorValueError as exc: return {"message": str(exc)}, 400
    reference_text = (payload.get("reference_text") or "").strip() or None
    result_status = evaluate_result_status(
        definition,
        value,
        subject=report.owner,
        on_date=report.exam_date,
        abnormal_flag=payload.get("abnormal_flag"),
        reference_text=reference_text,
    )
    row = ReportIndicator(report_id=report.id, indicator_dict_id=definition.id, value=value,
        is_abnormal=result_status in {"high", "low", "positive", "abnormal"},
        result_status=result_status,
        input_source=payload.get("input_source") if payload.get("input_source") in {"manual", "ocr"} else "manual",
        display_domain_id=display_domain_id, original_name=(payload.get("original_name") or definition.name).strip(),
        original_value=str(payload.get("original_value", payload.get("value"))),
        original_unit=(payload.get("original_unit") or definition.unit), normalized_unit=definition.unit,
        reference_text=reference_text,
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
    report, error = scoped_editable_report(report_id)
    if error: return error
    if report.status not in {"draft", "pending_review"}:
        return {"message": "published reports are immutable"}, 409
    row = ReportIndicator.query.filter_by(id=indicator_id, report_id=report.id).first()
    if not row: return {"message": "indicator not found"}, 404
    payload = request.get_json(silent=True) or {}
    definition = db.session.get(IndicatorDict, payload.get("indicator_dict_id", row.indicator_dict_id))
    if not definition: return {"message": "indicator not found"}, 404
    try: display_domain_id = admit_indicator(report, definition.id)
    except DomainAdmissionError as exc: return {"message": str(exc), "code": "DOMAIN_NOT_ALLOWED"}, 400
    try: value = normalize_indicator_value(definition, payload.get("value", row.value))
    except IndicatorValueError as exc: return {"message": str(exc)}, 400
    try: validate_indicator_plausibility(definition, value)
    except IndicatorValueError as exc: return {"message": str(exc)}, 400
    reference_text = (payload.get("reference_text", row.reference_text) or "").strip() or None
    abnormal_flag = (payload.get("abnormal_flag", row.abnormal_flag) or "").strip() or None
    row.indicator_dict_id = definition.id; row.value = value
    row.result_status = evaluate_result_status(
        definition,
        value,
        subject=report.owner,
        on_date=report.exam_date,
        abnormal_flag=abnormal_flag,
        reference_text=reference_text,
    )
    row.is_abnormal = row.result_status in {"high", "low", "positive", "abnormal"}
    row.display_domain_id = display_domain_id
    row.original_name = (payload.get("original_name") or row.original_name or definition.name).strip()
    row.original_value = str(payload.get("original_value", payload.get("value", row.original_value or value)))
    row.original_unit = payload.get("original_unit", row.original_unit or definition.unit); row.normalized_unit = definition.unit
    row.reference_text = reference_text; row.abnormal_flag = abnormal_flag
    row.method_snapshot = payload.get("method", row.method_snapshot)
    try: db.session.commit()
    except IntegrityError: db.session.rollback(); return {"message": "indicator already exists in report"}, 409
    return {"item": row.to_dict()}, 200


@org_bp.delete("/reports/<int:report_id>/indicators/<int:indicator_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def delete_indicator(report_id, indicator_id):
    report, error = scoped_editable_report(report_id)
    if error: return error
    if report.status not in {"draft", "pending_review"}:
        return {"message": "published reports are immutable"}, 409
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
    report, error = scoped_editable_report(report_id)
    if error: return error
    if report.status not in {"draft", "pending_review"}:
        return {"message": "published health data is immutable"}, 409
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
    report, error = scoped_editable_report(report_id)
    if error: return error
    if report.status not in {"draft", "pending_review"}:
        return {"message": "published health data is immutable"}, 409
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
    report, error = scoped_editable_report(report_id)
    if error: return error
    if report.status not in {"draft", "pending_review"}:
        return {"message": "published health data is immutable"}, 409
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
        format_name = image.format
        image.load()
        width, height = image.size
        clean_image = image.copy()
    expected = {"JPEG": ("image/jpeg", {".jpg", ".jpeg"}), "PNG": ("image/png", {".png"}), "WEBP": ("image/webp", {".webp"})}
    if format_name not in expected or extension not in expected[format_name][1]:
        raise ValueError("file extension does not match its actual image type")
    if width * height > current_app.config.get("HEALTH_ASSET_MAX_PIXELS", 40_000_000):
        raise ValueError("image pixel count exceeds the limit")
    if format_name == "JPEG" and clean_image.mode not in {"RGB", "L"}:
        clean_image = clean_image.convert("RGB")
    buffer = BytesIO()
    clean_image.save(buffer, format=format_name)
    Path(path).write_bytes(buffer.getvalue())
    return expected[format_name][0], width, height, None, os.path.getsize(path)


@org_bp.get("/report-asset-types")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_report_asset_types():
    query = ReportAssetType.query.filter_by(is_active=True)
    report_id = request.args.get("report_id", type=int)
    if report_id:
        report, error = scoped_report(report_id)
        if error: return error
        query = query.filter(ReportAssetType.health_domain_id.in_(report_allowed_domain_ids(report)))
    rows = query.order_by(ReportAssetType.sort_order, ReportAssetType.id).all()
    return {"items": [row.to_dict() for row in rows]}, 200


@org_bp.post("/health-data/<int:report_id>/assets")
@roles_required(ROLE_INSTITUTION_ADMIN)
def add_asset(report_id):
    report, error = scoped_editable_report(report_id)
    if error: return error
    if report.status not in {"draft", "pending_review"}:
        return {"message": "published health data is immutable"}, 409
    upload = request.files.get("file")
    if not upload or not upload.filename: return {"message": "file is required"}, 400
    extension = Path(upload.filename).suffix.lower()
    if extension not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}: return {"message": "unsupported file type"}, 400
    domain, error = _allowed_report_domain(report, request.form.get("health_domain_id"))
    if error: return error
    try: asset_type_id = int(request.form.get("asset_type_id"))
    except (TypeError, ValueError): return {"message": "请选择规范的检查附件类型"}, 400
    asset_type = ReportAssetType.query.filter_by(id=asset_type_id, is_active=True).first()
    if asset_type is None or asset_type.health_domain_id != domain.id:
        return {"message": "附件类型与健康方向不匹配"}, 400
    if len(report.assets) >= 12:
        return {"message": "每份报告最多上传 12 份检查附件"}, 409
    existing_count = ReportAsset.query.filter_by(
        report_id=report.id,
        asset_type_id=asset_type.id,
    ).count()
    if existing_count >= asset_type.max_files:
        return {"message": f"{asset_type.name}最多上传 {asset_type.max_files} 份"}, 409
    title = str(request.form.get("title") or asset_type.name).strip()
    modality = str(request.form.get("modality") or asset_type.modality or ("pdf" if extension == ".pdf" else "image")).strip()
    storage = get_storage_backend(current_app.config); saved = storage.save(upload, subdir="health-assets")
    try:
        mime, width, height, pages, size = _asset_metadata(saved["abs_path"], extension)
        digest = hashlib.sha256(Path(saved["abs_path"]).read_bytes()).hexdigest()
        row = ReportAsset(report_id=report.id, health_domain_id=domain.id, asset_type_id=asset_type.id, modality=modality,
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
    report, error = scoped_editable_report(report_id)
    if error: return error
    if report.status not in {"draft", "pending_review"}:
        return {"message": "published health data is immutable"}, 409
    row = ReportAsset.query.filter_by(id=asset_id, report_id=report.id).first()
    if not row: return {"message": "asset not found"}, 404
    payload = request.get_json(silent=True) or {}
    if "health_domain_id" in payload:
        domain, error = _allowed_report_domain(report, payload.get("health_domain_id"))
        if error: return error
        row.health_domain_id = domain.id
    if "asset_type_id" in payload:
        asset_type = ReportAssetType.query.filter_by(id=payload.get("asset_type_id"), is_active=True).first()
        if asset_type is None or asset_type.health_domain_id != row.health_domain_id:
            return {"message": "附件类型与健康方向不匹配"}, 400
        existing_count = ReportAsset.query.filter(
            ReportAsset.report_id == report.id,
            ReportAsset.asset_type_id == asset_type.id,
            ReportAsset.id != row.id,
        ).count()
        if existing_count >= asset_type.max_files:
            return {"message": f"{asset_type.name}已达到上传数量上限"}, 409
        row.asset_type_id = asset_type.id
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
    report, error = scoped_editable_report(report_id)
    if error: return error
    if report.status not in {"draft", "pending_review"}:
        return {"message": "published health data is immutable"}, 409
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
            try: validate_indicator_plausibility(definition, value)
            except IndicatorValueError:
                excluded.append({"field": candidate.get("raw_name") or definition.name, "reason": "implausible_value"})
                continue
            result_status = evaluate_result_status(
                definition,
                value,
                subject=report.owner,
                on_date=report.exam_date,
                abnormal_flag=candidate.get("abnormal_flag"),
                reference_text=candidate.get("reference_text"),
            )
            report.indicators.append(ReportIndicator(indicator_dict_id=definition.id, value=value,
                is_abnormal=result_status in {"high", "low", "positive", "abnormal"},
                result_status=result_status, input_source="ocr",
                display_domain_id=display_domain_id, original_name=candidate.get("raw_name") or definition.name,
                original_value=str(candidate.get("value")), original_unit=candidate.get("unit") or definition.unit,
                normalized_unit=definition.unit, reference_text=candidate.get("reference_text"),
                abnormal_flag=candidate.get("abnormal_flag"),
                mapping_confidence=candidate.get("score"), mapping_status="confirmed"))
            admitted_candidates.append(candidate)
        report.ocr_diagnostics = {**(report.ocr_diagnostics or {}), "excluded": excluded, "excluded_count": len(excluded)}
        db.session.commit()
    except Exception:
        db.session.rollback(); storage.delete(saved["key"]); raise
    return {"item": report.to_dict(include_indicators=True), "ocr": {"candidate_mappings": admitted_candidates, "excluded": excluded, "diagnostics": report.ocr_diagnostics}}, 201


def _doctor_name(payload, field):
    value = str(payload.get(field) or "").strip()
    if not value:
        return None, ({"message": f"{field} is required"}, 400)
    if len(value) > 80:
        return None, ({"message": f"{field} cannot exceed 80 characters"}, 400)
    return value, None


def _report_state_conflict(message):
    return {
        "message": message,
        "code": "REPORT_STATE_CONFLICT",
    }, 409


def _review_validation_error(report):
    if not report.indicators and not report.text_results and not report.assets:
        return {"message": "at least one indicator, text result or asset is required"}, 400
    try: validate_report_domains(report)
    except DomainAdmissionError as exc:
        return {"message": str(exc), "code": "DOMAIN_NOT_ALLOWED"}, 400
    missing_domains = missing_conclusion_domains(report)
    if missing_domains:
        return {
            "message": "请先完善所有健康方向的机构结论",
            "code": "MISSING_DOMAIN_CONCLUSIONS",
            "missing_domains": [
                {"id": domain.id, "name": domain.name}
                for domain in missing_domains
            ],
        }, 409
    requirements = PackageVersionAssetRequirement.query.filter_by(
        package_version_id=report.package_version_id,
        is_required=True,
    ).all() if report.package_version_id else []
    uploaded_types = {row.asset_type_id for row in report.assets if row.asset_type_id}
    missing = [
        requirement.asset_type.name
        for requirement in requirements
        if requirement.asset_type_id not in uploaded_types and requirement.asset_type
    ]
    if missing:
        return {"message": f"缺少必需检查附件：{'、'.join(missing)}", "code": "REQUIRED_ASSET_MISSING"}, 409
    if find_subject_user(report) is None:
        return {"message": "registered user not found or identity does not match"}, 409
    if report.appointment is not None and report.appointment.status != "awaiting_report":
        return _report_state_conflict("appointment is not awaiting a report")
    return None


def _submit_report_for_review(report_id):
    report, error = scoped_report(report_id, for_update=True)
    if error:
        return error
    if report.status != "draft":
        return _report_state_conflict(
            "only draft reports can be submitted for review"
        )
    payload = request.get_json(silent=True) or {}
    doctor_name, error = _doctor_name(payload, "upload_doctor_name")
    if error:
        return error
    if not report.indicators and not report.text_results and not report.assets:
        return {"message": "at least one indicator, text result or asset is required"}, 400
    submitted_at = datetime.now(timezone.utc)
    updated = InstitutionReport.query.filter(
        InstitutionReport.id == report.id,
        InstitutionReport.status == "draft",
    ).update(
        {
            InstitutionReport.status: "pending_review",
            InstitutionReport.upload_doctor_name: doctor_name,
            InstitutionReport.submitted_for_review_at: submitted_at,
        },
        synchronize_session=False,
    )
    if updated != 1:
        db.session.rollback()
        return _report_state_conflict(
            "report state changed; reload and retry"
        )
    if report.appointment_id is not None:
        db.session.add(AppointmentEvent(
            appointment_id=report.appointment_id,
            event_type="pending_review",
            status_snapshot=report.appointment.status,
            message=f"上传医生{doctor_name}已确认，报告待复核",
            actor_user_id=g.current_user.id,
            occurred_at=submitted_at,
        ))
    db.session.commit()
    db.session.refresh(report)
    return {"item": report.to_dict(include_indicators=True)}, 200


@org_bp.post("/reports/<int:report_id>/submit-review")
@roles_required(ROLE_INSTITUTION_ADMIN)
def submit_report_for_review(report_id):
    return _submit_report_for_review(report_id)


@org_bp.post("/reports/<int:report_id>/lock")
@roles_required(ROLE_INSTITUTION_ADMIN)
def legacy_lock_report(report_id):
    del report_id
    return {
        "message": "报告流程已升级，请使用待复核提交接口并填写上传医生姓名",
        "code": "REPORT_WORKFLOW_UPGRADED",
    }, 410


def _review_and_publish_report(report_id):
    report, error = scoped_report(report_id, for_update=True)
    if error:
        return error
    if report.status != "pending_review":
        return _report_state_conflict(
            "only reports pending review can be published"
        )
    payload = request.get_json(silent=True) or {}
    doctor_name, error = _doctor_name(payload, "review_doctor_name")
    if error:
        return error
    validation_error = _review_validation_error(report)
    if validation_error:
        return validation_error
    temp_url = report.temporary_file_url
    try:
        now = datetime.now(timezone.utc)
        owner = find_subject_user(report)
        if owner is None:
            return {
                "message": "registered user not found or identity does not match"
            }, 409
        sanitized_diagnostics = report.ocr_diagnostics
        if sanitized_diagnostics:
            sanitized_diagnostics = {
                key: value
                for key, value in sanitized_diagnostics.items()
                if key not in {"raw_text", "fields", "provider_response"}
            }
        # The row lock prevents concurrent publication on openGauss. The
        # compare-and-set is still required because SQLite does not implement
        # SELECT FOR UPDATE and because it makes the state transition explicit.
        updated = InstitutionReport.query.filter(
            InstitutionReport.id == report.id,
            InstitutionReport.institution_id == report.institution_id,
            InstitutionReport.status == "pending_review",
        ).update(
            {
                InstitutionReport.status: "published",
                InstitutionReport.review_doctor_name: doctor_name,
                InstitutionReport.reviewed_by_user_id: g.current_user.id,
                InstitutionReport.reviewed_by_username_snapshot:
                    g.current_user.username,
                InstitutionReport.reviewed_at: now,
                InstitutionReport.locked_at: now,
                InstitutionReport.temporary_file_url: None,
                InstitutionReport.ocr_diagnostics: sanitized_diagnostics,
                InstitutionReport.matched_user_id: owner.id,
                InstitutionReport.submitted_at: now,
                InstitutionReport.published_at: now,
            },
            synchronize_session=False,
        )
        if updated != 1:
            db.session.rollback()
            return _report_state_conflict(
                "report state changed; reload and retry"
            )
        if report.appointment is not None:
            report.appointment.status = "fulfilled"
            report.appointment.fulfilled_at = now
            from app.services.finance import schedule_settlement_for_appointment
            schedule_settlement_for_appointment(report.appointment, published_at=now)
            db.session.add(AppointmentEvent(
                appointment_id=report.appointment.id,
                event_type="report_published",
                status_snapshot="fulfilled",
                message=f"报告经{doctor_name}复核后发布",
                actor_user_id=g.current_user.id,
                occurred_at=now,
            ))
        if owner is not None:
            enqueue_user_notification(
                owner,
                event_type="report_published",
                idempotency_key=f"report:{report.id}:published",
                title="体检报告已交付",
                body=f"{report.exam_date.isoformat()} 在 {report.institution.name}·{report.institution.branch_name} 的体检报告已可查看。",
                action_url=f"/health-data/hd-i-{report.id:x}",
                payload={"report_id": report.id},
                email_payload={
                    "recipient_name": owner.real_name or "用户",
                    "institution": report.institution.name,
                    "branch": report.institution.branch_name,
                    "exam_date": report.exam_date.isoformat(),
                    "report_id": report.id,
                    "login_url": f"/health-data/{report.id}",
                },
            )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return _report_state_conflict(str(exc))
    except (IntegrityError, OperationalError):
        db.session.rollback()
        return _report_state_conflict(
            "report publishing conflict; reload and retry"
        )
    delete_report_urls([temp_url])
    db.session.refresh(report)
    return {
        "item": report.to_dict(include_indicators=True),
        "match_result": "matched",
    }, 200


@org_bp.post("/reports/<int:report_id>/review")
@roles_required(ROLE_INSTITUTION_ADMIN)
def review_and_publish_report(report_id):
    return _review_and_publish_report(report_id)


@org_bp.post("/reports/<int:report_id>/submit")
@roles_required(ROLE_INSTITUTION_ADMIN)
def legacy_submit_report(report_id):
    del report_id
    return {
        "message": "报告流程已升级，请使用复核接口并填写复核医生姓名",
        "code": "REPORT_WORKFLOW_UPGRADED",
    }, 410
