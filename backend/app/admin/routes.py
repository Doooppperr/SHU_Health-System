from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from flask import current_app, g, request
from sqlalchemy import func, or_, update
from sqlalchemy.exc import IntegrityError

from app.admin import admin_bp
from app.extensions import db
from app.models import Comment, Institution, InstitutionInvite, Organization, Package, PackageChangeRequest, User
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


def institution_payload(institution):
    payload = base_institution_payload(institution)
    payload["administrators"] = [item.to_dict(include_profile=False) for item in institution.administrators]
    payload["administrator_count"] = len(institution.administrators)
    payload["invite"] = institution.invite.to_dict() if institution.invite else None
    return payload


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
    item = Institution(organization_id=organization.id, name=organization.name)
    try:
        apply_institution_payload(item, payload, creating=True)
        db.session.add(item)
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback()
        return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback()
        return {"message": "institution branch already exists"}, 409
    return {"item": institution_payload(item)}, 201


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
    item.is_active = False
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
    item.is_active = True
    db.session.commit()
    return {"item": organization_payload(item, include_branches=True)}, 200


@admin_bp.post("/organizations/<int:organization_id>/branches")
@roles_required(ROLE_ADMIN)
def create_organization_branch(organization_id):
    organization, error = organization_or_error(organization_id)
    if error: return error
    payload = request.get_json(silent=True) or {}
    item = Institution(organization_id=organization.id, name=organization.name)
    try:
        apply_institution_payload(item, payload, creating=True)
        db.session.add(item); db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback(); return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback(); return {"message": "branch already exists in this organization"}, 409
    return {"item": institution_payload(item)}, 201


@admin_bp.get("/institutions/<int:institution_id>")
@roles_required(ROLE_ADMIN)
def get_institution(institution_id):
    item, error = institution_or_error(institution_id)
    return error if error else ({"item": institution_payload(item)}, 200)


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
    item.is_active = False
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
    item.is_active = True
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
    return paginated_items(
        InstitutionInvite.query.order_by(InstitutionInvite.issued_at.desc()),
        lambda item: item.to_dict(),
    )


@admin_bp.post("/institutions/<int:institution_id>/invite")
@roles_required(ROLE_ADMIN)
def issue_invite(institution_id):
    institution, error = institution_or_error(institution_id)
    if error:
        return error
    if not institution.is_active:
        return {"message": "cannot issue an invitation for an inactive institution"}, 400
    code = generate_invite_code()
    now = datetime.now(timezone.utc)
    invite = institution.invite
    if invite is None:
        invite = InstitutionInvite(institution_id=institution.id, code_hash=invite_hash(code), status="active", issued_by_admin_id=g.current_user.id, issued_at=now)
        db.session.add(invite)
    else:
        result = db.session.execute(
            update(InstitutionInvite).where(InstitutionInvite.id == invite.id, InstitutionInvite.code_hash == invite.code_hash).values(
                code_hash=invite_hash(code), status="active", issued_by_admin_id=g.current_user.id,
                used_by_user_id=None, issued_at=now, used_at=None,
            ).execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.session.rollback()
            return {"message": "invitation changed concurrently, please retry"}, 409
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {"message": "failed to issue invitation, please retry"}, 409
    return {"item": db.session.get(InstitutionInvite, invite.id).to_dict(), "invite_code": code}, 201


@admin_bp.delete("/institution-accounts/<int:user_id>")
@roles_required(ROLE_ADMIN)
def delete_institution_account(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.role != "institution_admin":
        return {"message": "institution account not found"}, 404
    db.session.delete(user)
    db.session.commit()
    return {"message": "institution account deleted; historical report snapshots were retained"}, 200
