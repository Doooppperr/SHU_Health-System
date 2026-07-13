from __future__ import annotations

from flask import g, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import HealthRecord, Institution, Package
from app.org import org_bp
from app.services.institution_management import (
    ManagementValidationError,
    apply_institution_payload,
    apply_package_payload,
    delete_institution_image,
    image_payload,
    institution_payload,
    reorder_institution_images,
    save_institution_image,
)
from app.services.permissions import ROLE_INSTITUTION_ADMIN, roles_required


def _managed_institution():
    institution_id = g.current_user.managed_institution_id
    institution = db.session.get(Institution, institution_id) if institution_id else None
    if institution is None:
        return None, ({"message": "managed institution not found"}, 403)
    if not institution.is_active:
        return None, ({"message": "managed institution is inactive"}, 403)
    return institution, None


def _managed_package(package_id: int):
    institution, error = _managed_institution()
    if error:
        return None, None, error
    package = Package.query.filter_by(id=package_id, institution_id=institution.id).first()
    if package is None:
        return institution, None, ({"message": "package not found"}, 404)
    return institution, package, None


@org_bp.get("/dashboard")
@roles_required(ROLE_INSTITUTION_ADMIN)
def dashboard():
    institution, error = _managed_institution()
    if error:
        return error
    profile_fields = (
        institution.name,
        institution.branch_name,
        institution.address,
        institution.district,
        institution.metro_info,
        institution.consult_phone,
        institution.closed_day,
        institution.description,
    )
    completed_fields = sum(bool(str(value or "").strip()) for value in profile_fields)
    records_query = HealthRecord.query.filter_by(
        institution_id=institution.id,
        status="confirmed",
    )
    recent = records_query.order_by(HealthRecord.exam_date.desc(), HealthRecord.id.desc()).limit(5).all()
    return {
        "summary": {
            "institution": institution_payload(institution),
            "profile_completion": round(completed_fields / len(profile_fields) * 100),
            "image_count": len(institution.images),
            "image_limit": 8,
            "active_package_count": Package.query.filter_by(
                institution_id=institution.id, is_active=True
            ).count(),
            "confirmed_record_count": records_query.count(),
            "owner_count": (
                db.session.query(func.count(func.distinct(HealthRecord.owner_id)))
                .filter(
                    HealthRecord.institution_id == institution.id,
                    HealthRecord.status == "confirmed",
                )
                .scalar()
                or 0
            ),
        },
        "recent_records": [
            {
                "id": item.id,
                "display_id": item.display_id,
                "owner_id": item.owner_id,
                "owner_display_name": item.owner.username if item.owner else None,
                "exam_date": item.exam_date.isoformat(),
                "status": item.status,
                "indicator_count": len(item.indicators),
            }
            for item in recent
        ],
    }, 200


@org_bp.get("/institution")
@roles_required(ROLE_INSTITUTION_ADMIN)
def get_institution():
    institution, error = _managed_institution()
    if error:
        return error
    return {"item": institution_payload(institution)}, 200


@org_bp.put("/institution")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_institution():
    institution, error = _managed_institution()
    if error:
        return error
    try:
        apply_institution_payload(institution, request.get_json(silent=True) or {})
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback()
        return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback()
        return {"message": "institution branch already exists"}, 409
    return {"item": institution_payload(institution)}, 200


@org_bp.get("/packages")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_packages():
    institution, error = _managed_institution()
    if error:
        return error
    items = Package.query.filter_by(institution_id=institution.id).order_by(Package.id.asc()).all()
    return {"items": [item.to_dict() for item in items]}, 200


@org_bp.post("/packages")
@roles_required(ROLE_INSTITUTION_ADMIN)
def create_package():
    institution, error = _managed_institution()
    if error:
        return error
    package = Package(institution_id=institution.id)
    try:
        apply_package_payload(package, request.get_json(silent=True) or {}, creating=True)
        db.session.add(package)
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback()
        return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback()
        return {"message": "package name already exists for the institution"}, 409
    return {"item": package.to_dict()}, 201


@org_bp.put("/packages/<int:package_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def update_package(package_id: int):
    _institution, package, error = _managed_package(package_id)
    if error:
        return error
    try:
        apply_package_payload(package, request.get_json(silent=True) or {})
        db.session.commit()
    except ManagementValidationError as exc:
        db.session.rollback()
        return {"message": str(exc)}, 400
    except IntegrityError:
        db.session.rollback()
        return {"message": "package name already exists for the institution"}, 409
    return {"item": package.to_dict()}, 200


@org_bp.delete("/packages/<int:package_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def deactivate_package(package_id: int):
    _institution, package, error = _managed_package(package_id)
    if error:
        return error
    package.is_active = False
    db.session.commit()
    return {"item": package.to_dict(), "message": "package deactivated"}, 200


@org_bp.get("/images")
@roles_required(ROLE_INSTITUTION_ADMIN)
def list_images():
    institution, error = _managed_institution()
    if error:
        return error
    return {"items": [image_payload(item) for item in institution.images], "limit": 8}, 200


@org_bp.post("/images")
@roles_required(ROLE_INSTITUTION_ADMIN)
def upload_image():
    institution, error = _managed_institution()
    if error:
        return error
    upload = request.files.get("file")
    if upload is None:
        return {"message": "image file is required"}, 400
    try:
        image = save_institution_image(institution, upload)
    except ManagementValidationError as exc:
        return {"message": str(exc)}, 400
    return {"item": image_payload(image)}, 201


@org_bp.put("/images/order")
@roles_required(ROLE_INSTITUTION_ADMIN)
def reorder_images():
    institution, error = _managed_institution()
    if error:
        return error
    try:
        images = reorder_institution_images(
            institution.id,
            (request.get_json(silent=True) or {}).get("image_ids"),
        )
    except ManagementValidationError as exc:
        db.session.rollback()
        return {"message": str(exc)}, 400
    return {"items": [image_payload(item) for item in images]}, 200


@org_bp.delete("/images/<int:image_id>")
@roles_required(ROLE_INSTITUTION_ADMIN)
def delete_image(image_id: int):
    institution, error = _managed_institution()
    if error:
        return error
    if not delete_institution_image(institution.id, image_id):
        return {"message": "institution image not found"}, 404
    return {"message": "institution image deleted"}, 200
