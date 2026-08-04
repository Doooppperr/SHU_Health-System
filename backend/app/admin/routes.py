from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, timezone

from flask import current_app, g, request
from sqlalchemy import func, or_, update
from sqlalchemy.exc import IntegrityError

from app.admin import admin_bp
from app.extensions import db
from app.models import (
    AppointmentComplaint, Comment, ComplaintEvent, ComplaintMessage, Institution,
    InstitutionInvite, NotificationOutbox, Organization, Package,
    PackageChangeRequest, User,
)
from app.services.account_email import effective_account_email
from app.services.contact import is_valid_email, normalize_email
from app.services.account_credentials import encrypt_account_credentials
from app.services.institution_management import (
    ManagementValidationError,
    apply_institution_payload,
    apply_package_payload,
    delete_institution_image,
    image_payload,
    institution_payload as base_institution_payload,
    reorder_institution_images,
    save_institution_image,
)
from app.services.permissions import ROLE_ADMIN, roles_required
from app.services.package_reviews import approve_change_request
from app.services.notifications import enqueue_user_notification
from app.services.password_challenges import (
    increment_user_security_epochs,
    revoke_account_security_artifacts,
)


def institution_payload(institution):
    payload = base_institution_payload(institution)
    payload["administrators"] = [item.to_dict(include_profile=False) for item in institution.administrators]
    payload["administrator_count"] = len(institution.administrators)
    payload["invite"] = institution.invite.to_dict() if institution.invite else None
    payload["account_delivery"] = account_delivery_payload(institution)
    return payload


def basic_identity_payload(user):
    """Administrator-safe identity view without health-history fields."""
    payload = user.to_dict(include_profile=False)
    if user.role == "user":
        payload.update({
            "health_id": user.health_id,
            "real_name": user.real_name,
            "birth_date": (
                user.birth_date.isoformat() if user.birth_date else None
            ),
            "gender": user.gender,
            "identity_completed": user.profile_completed,
            "identity_completed_at": (
                user.identity_completed_at.isoformat()
                if user.identity_completed_at
                else None
            ),
            "profile_completed": user.profile_completed,
            "profile_completed_at": (
                user.identity_completed_at.isoformat()
                if user.identity_completed_at
                else None
            ),
        })
    return payload


def _latest_account_outbox(institution):
    administrator = institution.administrator
    if administrator is None:
        return None
    return NotificationOutbox.query.filter(
        NotificationOutbox.event_type.in_((
            "institution_account_created",
            "institution_account_reset",
        )),
        NotificationOutbox.idempotency_key.like(
            f"institution-account:{administrator.id}:%"
        ),
    ).order_by(NotificationOutbox.id.desc()).first()


def account_delivery_payload(institution):
    row = _latest_account_outbox(institution)
    if row is None:
        return None
    return {
        "outbox_id": row.id,
        "status": row.status,
        "attempts": row.attempts,
        "recipient": row.recipient,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "sensitive_payload_cleared_at": (
            row.sensitive_payload_cleared_at.isoformat()
            if row.sensitive_payload_cleared_at
            else None
        ),
    }


def _account_fields(payload):
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    email = normalize_email(payload.get("email"))
    if not username or not password or not email:
        raise ManagementValidationError("新建分院时必须填写机构账号用户名、密码和邮箱")
    if len(username) > 80:
        raise ManagementValidationError("机构账号用户名不能超过80个字符")
    if len(password) < 8 or len(password) > 128:
        raise ManagementValidationError("机构账号密码长度应为8至128位")
    if not is_valid_email(email):
        raise ManagementValidationError("请输入有效的机构邮箱")
    if User.query.filter_by(username=username).first() is not None:
        raise ManagementValidationError("该机构账号用户名已被使用")
    return username, password, email


def _enqueue_account_credentials(institution, administrator, password, event_type):
    label = (
        f"{institution.organization.name}·{institution.branch_name}"
        if institution.organization else institution.branch_name
    )
    purpose = f"institution-account:{administrator.id}:version:{administrator.token_version}"
    encrypted = encrypt_account_credentials(
        {
            "username": administrator.username,
            "temporary_password": password,
        },
        purpose=purpose,
    )
    public_app_url = str(current_app.config.get("PUBLIC_APP_URL") or "").rstrip("/")
    login_url = f"{public_app_url}/login" if public_app_url else "/login"
    row = NotificationOutbox(
        event_type=event_type,
        idempotency_key=(
            f"institution-account:{administrator.id}:"
            f"version:{administrator.token_version}:{event_type}"
        ),
        recipient=institution.notification_email,
        payload={
            "encrypted_credentials": encrypted,
            "credential_purpose": purpose,
            "account_label": label,
            "login_url": login_url,
            "message": "请在首次登录后尽快修改密码。",
        },
    )
    db.session.add(row)
    return row


def _supersede_unsent_account_credentials(administrator):
    """Permanently retire credentials made invalid by a password reset.

    A failed or delayed creation email must never be retried after a newer
    temporary password has replaced it. The outbox has no general cancelled
    state, so retain the audit row as a terminal failed delivery while clearing
    its encrypted secret and moving its retry time outside the runnable range.
    """
    now = datetime.now(timezone.utc)
    rows = NotificationOutbox.query.filter(
        NotificationOutbox.event_type.in_((
            "institution_account_created",
            "institution_account_reset",
        )),
        NotificationOutbox.idempotency_key.like(
            f"institution-account:{administrator.id}:%"
        ),
        NotificationOutbox.status.in_(("pending", "failed", "sending")),
    ).all()
    for row in rows:
        row.status = "failed"
        row.next_attempt_at = datetime.max.replace(tzinfo=timezone.utc)
        row.payload = {
            "sensitive_content_cleared": True,
            "superseded_by_password_reset": True,
        }
        row.sensitive_payload_cleared_at = now
    return len(rows)


def _create_institution_with_account(organization, payload):
    username, password, email = _account_fields(payload)
    item = Institution(
        organization_id=organization.id,
        name=organization.name,
        notification_email=email,
    )
    apply_institution_payload(item, payload, creating=True)
    db.session.add(item)
    db.session.flush()
    administrator = User(
        username=username,
        email=email,
        role="institution_admin",
        managed_institution_id=item.id,
    )
    administrator.set_password(password)
    administrator.must_change_initial_password = True
    db.session.add(administrator)
    db.session.flush()
    outbox = _enqueue_account_credentials(
        item,
        administrator,
        password,
        "institution_account_created",
    )
    return item, administrator, outbox


def organization_payload(organization, *, include_branches=False):
    payload = organization.to_dict(include_branches=include_branches)
    if include_branches:
        payload["branches"] = [institution_payload(branch) for branch in organization.branches]
    return payload


def organization_or_error(organization_id):
    item = db.session.get(Organization, organization_id)
    return (item, None) if item else (None, ({"message": "organization not found"}, 404))


def apply_organization_payload(item, payload, *, creating=False):
    if not isinstance(payload, dict):
        raise ManagementValidationError("request body must be an object")
    if creating or "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ManagementValidationError("organization name is required")
        item.name = name
    if "description" in payload:
        item.description = str(payload.get("description") or "").strip() or None
    if "service_features" in payload:
        features = payload.get("service_features")
        if not isinstance(features, list) or any(not str(value).strip() for value in features):
            raise ManagementValidationError("service_features must be a list of non-empty text values")
        item.service_features = [str(value).strip() for value in features]
    return item


def invite_hash(code):
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def generate_invite_code():
    size = max(int(current_app.config.get("INVITE_CODE_BYTES", 8)), 4)
    compact = secrets.token_hex(size).upper()
    return "-".join(compact[index:index + 4] for index in range(0, len(compact), 4))


def institution_or_error(institution_id):
    item = db.session.get(Institution, institution_id)
    return (item, None) if item else (None, ({"message": "institution not found"}, 404))


def paginated_items(query, serializer, default_size=15):
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", default_size, type=int) or default_size, 1), 100)
    total = query.count()
    rows = query.offset((page - 1) * size).limit(size).all()
    return {
        "items": [serializer(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200


@admin_bp.get("/dashboard")
@roles_required(ROLE_ADMIN)
def dashboard():
    roles = dict(db.session.query(User.role, func.count(User.id)).group_by(User.role).all())
    institution_count = Institution.query.count()
    return {
        "summary": {
            "account_count": User.query.count(),
            "regular_user_count": roles.get("user", 0),
            "institution_account_count": roles.get("institution_admin", 0),
            "institution_count": institution_count,
            "organization_count": Organization.query.count(),
            "active_institution_count": Institution.query.filter_by(is_active=True).count(),
            "pending_comment_count": Comment.query.filter_by(is_visible=False).count(),
            "pending_complaint_count": AppointmentComplaint.query.filter(
                AppointmentComplaint.status != "resolved",
            ).count(),
            "pending_package_review_count": PackageChangeRequest.query.filter_by(status="pending").count(),
        }
    }, 200


@admin_bp.get("/institutions")
@roles_required(ROLE_ADMIN)
def list_institutions():
    query = Institution.query.join(Institution.organization)
    keyword = (request.args.get("keyword") or "").strip()
    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter(or_(Organization.name.ilike(pattern), Institution.branch_name.ilike(pattern), Institution.district.ilike(pattern)))
    active = (request.args.get("is_active") or "").lower()
    if active in {"true", "1"}:
        query = query.filter_by(is_active=True)
    elif active in {"false", "0"}:
        query = query.filter_by(is_active=False)
    return paginated_items(query.order_by(Institution.id), institution_payload)


@admin_bp.post("/institutions")
@roles_required(ROLE_ADMIN)
def create_institution():
    payload = request.get_json(silent=True) or {}
    try:
        organization_id = int(payload.get("organization_id"))
    except (TypeError, ValueError):
        return {"message": "organization_id is required"}, 400
    organization = db.session.get(Organization, organization_id)
    if organization is None:
        return {"message": "organization not found"}, 404
    try:
        item, administrator, outbox = _create_institution_with_account(
            organization,
            payload,
        )
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback()
        return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback()
        return {"message": "institution branch already exists"}, 409
    return {
        "item": institution_payload(item),
        "account": administrator.to_dict(include_profile=False),
        "delivery": {
            "outbox_id": outbox.id,
            "status": outbox.status,
        },
    }, 201


@admin_bp.get("/organizations")
@roles_required(ROLE_ADMIN)
def list_organizations():
    return {"items": [organization_payload(item, include_branches=True) for item in Organization.query.order_by(Organization.id).all()]}, 200


@admin_bp.post("/organizations")
@roles_required(ROLE_ADMIN)
def create_organization():
    item = Organization()
    try:
        apply_organization_payload(item, request.get_json(silent=True) or {}, creating=True)
        db.session.add(item)
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback(); return {"message": "organization already exists"}, 409
    return {"item": organization_payload(item, include_branches=True)}, 201


@admin_bp.get("/organizations/<int:organization_id>")
@roles_required(ROLE_ADMIN)
def get_organization(organization_id):
    item, error = organization_or_error(organization_id)
    return error if error else ({"item": organization_payload(item, include_branches=True)}, 200)


@admin_bp.put("/organizations/<int:organization_id>")
@roles_required(ROLE_ADMIN)
def update_organization(organization_id):
    item, error = organization_or_error(organization_id)
    if error: return error
    try:
        apply_organization_payload(item, request.get_json(silent=True) or {})
        for branch in item.branches:
            branch.name = item.name
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback(); return {"message": "organization already exists"}, 409
    return {"item": organization_payload(item, include_branches=True)}, 200


@admin_bp.post("/organizations/<int:organization_id>/deactivate")
@roles_required(ROLE_ADMIN)
def deactivate_organization(organization_id):
    item, error = organization_or_error(organization_id)
    if error: return error
    if item.is_active:
        changed_at = datetime.now(timezone.utc)
        item.is_active = False
        administrator_ids = sorted({
            administrator.id
            for branch in item.branches
            for administrator in branch.administrators
        })
        increment_user_security_epochs(administrator_ids)
        for administrator_id in administrator_ids:
            revoke_account_security_artifacts(
                administrator_id,
                revoked_at=changed_at,
            )
    for branch in item.branches:
        if branch.invite and branch.invite.status == "active":
            branch.invite.status = "superseded"
    db.session.commit()
    return {"item": organization_payload(item, include_branches=True)}, 200


@admin_bp.post("/organizations/<int:organization_id>/restore")
@roles_required(ROLE_ADMIN)
def restore_organization(organization_id):
    item, error = organization_or_error(organization_id)
    if error: return error
    if not item.is_active:
        changed_at = datetime.now(timezone.utc)
        item.is_active = True
        # Restoration is a new security epoch. Credentials that were rejected
        # while the organization was disabled must never become valid again.
        administrator_ids = sorted({
            administrator.id
            for branch in item.branches
            for administrator in branch.administrators
        })
        increment_user_security_epochs(administrator_ids)
        for administrator_id in administrator_ids:
            revoke_account_security_artifacts(
                administrator_id,
                revoked_at=changed_at,
            )
    db.session.commit()
    return {"item": organization_payload(item, include_branches=True)}, 200


@admin_bp.post("/organizations/<int:organization_id>/branches")
@roles_required(ROLE_ADMIN)
def create_organization_branch(organization_id):
    organization, error = organization_or_error(organization_id)
    if error: return error
    payload = request.get_json(silent=True) or {}
    try:
        item, administrator, outbox = _create_institution_with_account(
            organization,
            payload,
        )
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback(); return {"message": "branch already exists in this organization"}, 409
    return {
        "item": institution_payload(item),
        "account": administrator.to_dict(include_profile=False),
        "delivery": {"outbox_id": outbox.id, "status": outbox.status},
    }, 201


@admin_bp.get("/institutions/<int:institution_id>")
@roles_required(ROLE_ADMIN)
def get_institution(institution_id):
    item, error = institution_or_error(institution_id)
    return error if error else ({"item": institution_payload(item)}, 200)


@admin_bp.get("/institutions/<int:institution_id>/account")
@roles_required(ROLE_ADMIN)
def get_institution_account(institution_id):
    item, error = institution_or_error(institution_id)
    if error:
        return error
    administrator = item.administrator
    if administrator is None:
        return {"message": "该分院尚未配置机构账号"}, 404
    return {
        "account": administrator.to_dict(include_profile=False),
        "delivery": account_delivery_payload(item),
    }, 200


@admin_bp.post("/institutions/<int:institution_id>/account-notification/retry")
@roles_required(ROLE_ADMIN)
def retry_institution_account_notification(institution_id):
    item, error = institution_or_error(institution_id)
    if error:
        return error
    row = _latest_account_outbox(item)
    if row is None:
        return {"message": "没有可重试的机构账号通知"}, 404
    if row.status == "sent" or not (row.payload or {}).get("encrypted_credentials"):
        return {
            "message": "临时密码已在发送成功后清除，请重置密码后再次发送",
            "code": "CREDENTIALS_CLEARED",
        }, 409
    if row.status == "sending":
        return {"message": "账号通知正在发送，请稍后查询状态"}, 409
    row.status = "pending"
    row.next_attempt_at = datetime.now(timezone.utc)
    db.session.commit()
    return {
        "message": "机构账号通知已重新进入发送队列",
        "delivery": account_delivery_payload(item),
    }, 200


@admin_bp.post("/institutions/<int:institution_id>/account/reset")
@roles_required(ROLE_ADMIN)
def reset_institution_account(institution_id):
    item, error = institution_or_error(institution_id)
    if error:
        return error
    administrator = item.administrator
    if administrator is None:
        return {"message": "该分院尚未配置机构账号"}, 404
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password") or "")
    if len(password) < 8 or len(password) > 128:
        return {"message": "新密码长度应为8至128位"}, 400
    email = (
        normalize_email(payload.get("email"))
        if "email" in payload
        else item.notification_email
    )
    if not email or not is_valid_email(email):
        return {"message": "请输入有效的机构邮箱"}, 400
    item.notification_email = email
    administrator.email = email
    administrator.email_verified_at = None
    _supersede_unsent_account_credentials(administrator)
    administrator.set_password(password)
    administrator.must_change_initial_password = True
    increment_user_security_epochs(administrator.id)
    revoke_account_security_artifacts(administrator.id)
    outbox = _enqueue_account_credentials(
        item,
        administrator,
        password,
        "institution_account_reset",
    )
    db.session.commit()
    return {
        "message": "机构账号凭据已重置，通知邮件已进入发送队列",
        "account": administrator.to_dict(include_profile=False),
        "delivery": {"outbox_id": outbox.id, "status": outbox.status},
    }, 200


@admin_bp.put("/institutions/<int:institution_id>")
@roles_required(ROLE_ADMIN)
def update_institution(institution_id):
    item, error = institution_or_error(institution_id)
    if error:
        return error
    try:
        apply_institution_payload(item, request.get_json(silent=True) or {})
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback()
        return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback()
        return {"message": "institution branch already exists"}, 409
    return {"item": institution_payload(item)}, 200


@admin_bp.post("/institutions/<int:institution_id>/deactivate")
@roles_required(ROLE_ADMIN)
def deactivate_institution(institution_id):
    item, error = institution_or_error(institution_id)
    if error:
        return error
    if item.is_active:
        changed_at = datetime.now(timezone.utc)
        item.is_active = False
        administrator_ids = sorted(
            administrator.id for administrator in item.administrators
        )
        increment_user_security_epochs(administrator_ids)
        for administrator_id in administrator_ids:
            revoke_account_security_artifacts(
                administrator_id,
                revoked_at=changed_at,
            )
    if item.invite and item.invite.status == "active":
        item.invite.status = "superseded"
    db.session.commit()
    return {"item": institution_payload(item)}, 200


@admin_bp.post("/institutions/<int:institution_id>/restore")
@roles_required(ROLE_ADMIN)
def restore_institution(institution_id):
    item, error = institution_or_error(institution_id)
    if error:
        return error
    if not item.is_active:
        changed_at = datetime.now(timezone.utc)
        item.is_active = True
        item.account_deactivated_at = None
        for administrator in item.administrators:
            administrator.is_active = True
        administrator_ids = sorted(
            administrator.id for administrator in item.administrators
        )
        increment_user_security_epochs(administrator_ids)
        for administrator_id in administrator_ids:
            revoke_account_security_artifacts(
                administrator_id,
                revoked_at=changed_at,
            )
    db.session.commit()
    return {"item": institution_payload(item)}, 200


@admin_bp.get("/institutions/<int:institution_id>/packages")
@roles_required(ROLE_ADMIN)
def list_packages(institution_id):
    item, error = institution_or_error(institution_id)
    if error:
        return error
    return paginated_items(
        Package.query.filter_by(institution_id=item.id).order_by(Package.id),
        lambda package: package.to_dict(),
    )


@admin_bp.post("/institutions/<int:institution_id>/packages")
@roles_required(ROLE_ADMIN)
def create_package(institution_id):
    return {"message": "管理员不能直接新增套餐，请审核机构提交的变更申请"}, 405


@admin_bp.put("/institutions/<int:institution_id>/packages/<int:package_id>")
@roles_required(ROLE_ADMIN)
def update_package(institution_id, package_id):
    return {"message": "管理员不能直接修改套餐，请审核机构提交的变更申请"}, 405


@admin_bp.delete("/institutions/<int:institution_id>/packages/<int:package_id>")
@roles_required(ROLE_ADMIN)
def deactivate_package(institution_id, package_id):
    return {"message": "管理员不能直接上下架套餐，请审核机构提交的变更申请"}, 405


@admin_bp.get("/package-change-requests")
@roles_required(ROLE_ADMIN)
def list_package_change_requests():
    query = PackageChangeRequest.query
    status = (request.args.get("status") or "").strip()
    if status:
        query = query.filter_by(status=status)
    return paginated_items(
        query.order_by(PackageChangeRequest.requested_at.desc(), PackageChangeRequest.id.desc()),
        lambda item: item.to_dict(),
    )


def _pending_change_request(request_id):
    item = PackageChangeRequest.query.filter_by(id=request_id).with_for_update().first()
    if item is None:
        return None, ({"message": "review request not found"}, 404)
    if item.status != "pending":
        return None, ({"message": "only pending requests can be reviewed"}, 409)
    return item, None


@admin_bp.post("/package-change-requests/<int:request_id>/approve")
@roles_required(ROLE_ADMIN)
def approve_package_change(request_id):
    item, error = _pending_change_request(request_id)
    if error:
        return error
    try:
        approve_change_request(item, g.current_user, (request.get_json(silent=True) or {}).get("review_note"))
        item.reviewed_at = datetime.now(timezone.utc)
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 409
    except IntegrityError:
        db.session.rollback(); return {"message": "套餐名称冲突，申请无法生效"}, 409
    return {"item": item.to_dict()}, 200


@admin_bp.post("/package-change-requests/<int:request_id>/reject")
@roles_required(ROLE_ADMIN)
def reject_package_change(request_id):
    item, error = _pending_change_request(request_id)
    if error:
        return error
    item.status = "rejected"
    item.reviewed_by_user_id = g.current_user.id
    item.review_note = str((request.get_json(silent=True) or {}).get("review_note") or "").strip() or None
    item.reviewed_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"item": item.to_dict()}, 200


@admin_bp.get("/institutions/<int:institution_id>/images")
@roles_required(ROLE_ADMIN)
def list_images(institution_id):
    item, error = institution_or_error(institution_id)
    return error if error else ({"items": [image_payload(image) for image in item.images], "limit": 8}, 200)


@admin_bp.post("/institutions/<int:institution_id>/images")
@roles_required(ROLE_ADMIN)
def upload_image(institution_id):
    item, error = institution_or_error(institution_id)
    if error:
        return error
    upload = request.files.get("file")
    if not upload:
        return {"message": "image file is required"}, 400
    try:
        image = save_institution_image(item, upload)
    except ManagementValidationError as exc:
        return {"message": str(exc)}, 400
    return {"item": image_payload(image)}, 201


@admin_bp.put("/institutions/<int:institution_id>/images/order")
@roles_required(ROLE_ADMIN)
def reorder_images(institution_id):
    try:
        images = reorder_institution_images(institution_id, (request.get_json(silent=True) or {}).get("image_ids"))
    except ManagementValidationError as exc:
        db.session.rollback()
        return {"message": str(exc)}, 400
    return {"items": [image_payload(item) for item in images]}, 200


@admin_bp.delete("/institutions/<int:institution_id>/images/<int:image_id>")
@roles_required(ROLE_ADMIN)
def delete_image(institution_id, image_id):
    if not delete_institution_image(institution_id, image_id):
        return {"message": "institution image not found"}, 404
    return {"message": "institution image deleted"}, 200


@admin_bp.get("/invites")
@roles_required(ROLE_ADMIN)
def list_invites():
    return {
        "message": "机构邀请码注册已停用，请在新建分院时直接配置唯一机构账号",
        "code": "INSTITUTION_INVITES_DISABLED",
    }, 410


@admin_bp.post("/institutions/<int:institution_id>/invite")
@roles_required(ROLE_ADMIN)
def issue_invite(institution_id):
    del institution_id
    return {
        "message": "机构邀请码注册已停用，请在新建分院时直接配置唯一机构账号",
        "code": "INSTITUTION_INVITES_DISABLED",
    }, 410


def _platform_complaint(complaint_id):
    item = db.session.get(AppointmentComplaint, complaint_id)
    if item is None:
        return None, ({"message": "未找到该投诉记录"}, 404)
    return item, None


def _transition_platform_complaint_cas(
    item,
    *,
    expected_status,
    next_status,
    changed_at,
    values=None,
):
    update_values = dict(values or {})
    update_values.update({
        AppointmentComplaint.status: next_status,
        AppointmentComplaint.updated_at: changed_at,
    })
    changed = AppointmentComplaint.query.filter(
        AppointmentComplaint.id == item.id,
        AppointmentComplaint.status == expected_status,
    ).update(update_values, synchronize_session=False)
    return changed == 1


def _update_platform_complaint_reply_cas(item, *, content, admin_id, replied_at):
    changed = AppointmentComplaint.query.filter(
        AppointmentComplaint.id == item.id,
        AppointmentComplaint.status == "platform_processing",
    ).update(
        {
            AppointmentComplaint.admin_reply: content,
            AppointmentComplaint.handled_by_admin_id: admin_id,
            AppointmentComplaint.handled_at: replied_at,
            AppointmentComplaint.updated_at: replied_at,
        },
        synchronize_session=False,
    )
    return changed == 1


def _notify_complainant(item, *, event_type, title, body):
    if item.complainant is None:
        return
    enqueue_user_notification(
        item.complainant,
        event_type=event_type,
        idempotency_key=f"complaint:{item.id}:{event_type}",
        title=title,
        body=body,
        action_url=f"/appointments?complaint_id={item.id}",
        payload={"complaint_id": item.id},
    )


def _notify_complaint_institution(item, *, event_type, title, body):
    administrator = item.institution.administrator if item.institution else None
    if administrator is None or not administrator.is_active:
        return
    enqueue_user_notification(
        administrator,
        event_type=event_type,
        idempotency_key=f"complaint:{item.id}:{event_type}:institution",
        title=title,
        body=body,
        action_url=f"/org/complaints?complaint_id={item.id}",
        payload={"complaint_id": item.id},
    )


@admin_bp.get("/complaints")
@roles_required(ROLE_ADMIN)
def list_platform_complaints():
    query = AppointmentComplaint.query
    status = str(request.args.get("status") or "").strip()
    if status:
        query = query.filter_by(status=status)
    institution_id = request.args.get("institution_id", type=int)
    if institution_id:
        query = query.filter_by(institution_id=institution_id)
    return paginated_items(
        query.order_by(
            AppointmentComplaint.updated_at.desc(),
            AppointmentComplaint.id.desc(),
        ),
        lambda row: row.to_dict(),
    )


@admin_bp.post("/complaints/<int:complaint_id>/start")
@roles_required(ROLE_ADMIN)
def start_platform_complaint(complaint_id):
    item, error = _platform_complaint(complaint_id)
    if error:
        return error
    if item.status != "platform_pending":
        return {
            "message": "只有待平台处理的投诉可以开始处理",
            "code": "COMPLAINT_STATE_CONFLICT",
        }, 409
    now = datetime.now(timezone.utc)
    if not _transition_platform_complaint_cas(
        item,
        expected_status="platform_pending",
        next_status="platform_processing",
        changed_at=now,
        values={
            AppointmentComplaint.handled_by_admin_id: g.current_user.id,
            AppointmentComplaint.handled_at: now,
        },
    ):
        db.session.rollback()
        return {
            "message": "投诉状态已变化，请刷新后重试",
            "code": "COMPLAINT_STATE_CONFLICT",
        }, 409
    db.session.add(ComplaintEvent(
        complaint_id=item.id,
        event_type="admin_started",
        actor_user_id=g.current_user.id,
        actor_role=g.current_user.role,
        content="平台管理员已开始处理",
        created_at=now,
    ))
    _notify_complainant(
        item,
        event_type="complaint_admin_started",
        title="平台已受理您的投诉",
        body=f"投诉 #{item.id} 已由平台管理员开始处理。",
    )
    _notify_complaint_institution(
        item,
        event_type="complaint_admin_started",
        title="平台已开始处理投诉",
        body=f"投诉 #{item.id} 已进入平台处理流程，机构端保持只读。",
    )
    db.session.commit()
    return {"item": item.to_dict(), "message": "已开始处理投诉"}, 200


@admin_bp.post("/complaints/<int:complaint_id>/reply")
@roles_required(ROLE_ADMIN)
def reply_platform_complaint(complaint_id):
    item, error = _platform_complaint(complaint_id)
    if error:
        return error
    if item.status != "platform_processing":
        return {
            "message": "请先开始处理该投诉",
            "code": "COMPLAINT_STATE_CONFLICT",
        }, 409
    content = str((request.get_json(silent=True) or {}).get("content") or "").strip()
    if not content:
        return {"message": "请填写平台处理回复"}, 400
    if len(content) > 2000:
        return {"message": "平台处理回复不能超过2000个字符"}, 400
    now = datetime.now(timezone.utc)
    if not _update_platform_complaint_reply_cas(
        item,
        content=content,
        admin_id=g.current_user.id,
        replied_at=now,
    ):
        db.session.rollback()
        return {
            "message": "投诉状态已变化，请刷新后重试",
            "code": "COMPLAINT_STATE_CONFLICT",
        }, 409
    db.session.add(ComplaintEvent(
        complaint_id=item.id,
        event_type="admin_replied",
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
    _notify_complainant(
        item,
        event_type="complaint_admin_replied",
        title="平台已回复您的投诉",
        body="平台管理员已给出处理回复，请查看投诉详情。",
    )
    db.session.commit()
    return {"item": item.to_dict(), "message": "平台回复已提交"}, 200


@admin_bp.post("/complaints/<int:complaint_id>/resolve")
@roles_required(ROLE_ADMIN)
def resolve_platform_complaint(complaint_id):
    item, error = _platform_complaint(complaint_id)
    if error:
        return error
    if item.status != "platform_processing":
        return {
            "message": "只有平台处理中的投诉可以关闭",
            "code": "COMPLAINT_STATE_CONFLICT",
        }, 409
    if not str(item.admin_reply or "").strip():
        return {
            "message": "请先回复用户，再关闭投诉",
            "code": "COMPLAINT_STATE_CONFLICT",
        }, 409
    now = datetime.now(timezone.utc)
    if not _transition_platform_complaint_cas(
        item,
        expected_status="platform_processing",
        next_status="resolved",
        changed_at=now,
        values={
            AppointmentComplaint.resolved_at: now,
            AppointmentComplaint.handled_by_admin_id: g.current_user.id,
            AppointmentComplaint.handled_at: now,
        },
    ):
        db.session.rollback()
        return {
            "message": "投诉状态已变化，请刷新后重试",
            "code": "COMPLAINT_STATE_CONFLICT",
        }, 409
    db.session.add(ComplaintEvent(
        complaint_id=item.id,
        event_type="admin_resolved",
        actor_user_id=g.current_user.id,
        actor_role=g.current_user.role,
        content="平台管理员关闭投诉并标记为已解决",
        created_at=now,
    ))
    _notify_complainant(
        item,
        event_type="complaint_admin_resolved",
        title="投诉已由平台处理完成",
        body=f"投诉 #{item.id} 已关闭并标记为已解决。",
    )
    _notify_complaint_institution(
        item,
        event_type="complaint_admin_resolved",
        title="平台投诉处理已完成",
        body=f"投诉 #{item.id} 已由平台关闭并标记为已解决。",
    )
    db.session.commit()
    return {"item": item.to_dict(), "message": "投诉已关闭并标记为已解决"}, 200


@admin_bp.put("/users/<int:user_id>/basic-profile")
@roles_required(ROLE_ADMIN)
def correct_user_basic_profile(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.role != "user":
        return {"message": "未找到该普通用户"}, 404
    recipient = effective_account_email(user)
    if not recipient or not is_valid_email(recipient):
        return {
            "message": "该用户没有有效通知邮箱，无法发送实名认证修正通知",
            "code": "IDENTITY_CORRECTION_EMAIL_REQUIRED",
        }, 409
    payload = request.get_json(silent=True) or {}
    allowed = {"real_name", "birth_date", "gender"}
    if not allowed.intersection(payload):
        return {"message": "请提供姓名、出生日期或性别修正项"}, 400
    if set(payload) - allowed:
        return {
            "message": "该接口仅允许修正姓名、出生日期和性别，不提供健康档案访问",
            "code": "BASIC_PROFILE_FIELDS_ONLY",
        }, 400
    changed_fields = []
    if "real_name" in payload:
        real_name = str(payload.get("real_name") or "").strip()
        if not real_name or len(real_name) > 80:
            return {"message": "姓名不能为空且不能超过80个字符"}, 400
        if real_name != user.real_name:
            user.real_name = real_name
            changed_fields.append("姓名")
    if "birth_date" in payload:
        try:
            birth_date = date.fromisoformat(str(payload.get("birth_date") or ""))
        except (TypeError, ValueError):
            return {"message": "出生日期格式应为 YYYY-MM-DD"}, 400
        if birth_date > date.today() or birth_date.year < 1900:
            return {"message": "出生日期不在允许范围内"}, 400
        if birth_date != user.birth_date:
            user.birth_date = birth_date
            changed_fields.append("出生日期")
    if "gender" in payload:
        gender = str(payload.get("gender") or "").strip()
        if gender not in {"male", "female", "other", "undisclosed"}:
            return {"message": "请选择有效的性别"}, 400
        if gender != user.gender:
            user.gender = gender
            changed_fields.append("性别")
    if not changed_fields:
        return {
            "item": basic_identity_payload(user),
            "message": "基本信息没有变化",
        }, 200
    now = datetime.now(timezone.utc)
    if (
        (user.real_name or "").strip()
        and user.birth_date is not None
        and user.gender in {"male", "female", "other", "undisclosed"}
    ):
        user.identity_completed_at = user.identity_completed_at or now
    fields_text = "、".join(changed_fields)
    outbox = NotificationOutbox(
        event_type="user_identity_corrected",
        idempotency_key=(
            f"user:{user.id}:identity-corrected:"
            f"{int(now.timestamp() * 1_000_000)}"
        ),
        recipient=recipient,
        payload={
            "recipient_name": user.real_name or "用户",
            "message": f"平台管理员已修正您的{fields_text}。如有疑问，请联系平台。",
            "login_url": "/profile",
            "changed_fields": changed_fields,
        },
    )
    db.session.add(outbox)
    db.session.commit()
    return {
        "item": basic_identity_payload(user),
        "changed_fields": changed_fields,
        "delivery": {"outbox_id": outbox.id, "status": outbox.status},
        "message": "用户基本信息已修正，邮件通知已进入发送队列；历史预约和报告快照保持不变",
    }, 200


@admin_bp.delete("/institution-accounts/<int:user_id>")
@roles_required(ROLE_ADMIN)
def delete_institution_account(user_id):
    del user_id
    return {
        "message": "机构账号硬删除已停用；请由机构完成注销条件后软注销，管理员可恢复",
        "code": "INSTITUTION_ACCOUNT_SOFT_DEACTIVATION_REQUIRED",
    }, 410
