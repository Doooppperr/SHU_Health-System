from flask_jwt_extended import jwt_required

from app.extensions import db
from app.institutions import institutions_bp
from app.models import Institution, Package
from app.public_api.routes import (
    public_branch_payload,
    public_package_payload,
)


@institutions_bp.get("")
@jwt_required()
def list_institutions():
    institutions = (
        Institution.query.join(Institution.organization).filter(
            Institution.is_active.is_(True),
            Institution.organization.has(is_active=True),
        )
        .order_by(Institution.id.asc())
        .all()
    )
    return {"items": [public_branch_payload(item) for item in institutions]}, 200


@institutions_bp.get("/<int:institution_id>")
@jwt_required()
def get_institution_detail(institution_id: int):
    institution = db.session.get(Institution, institution_id)
    if institution is None or not institution.is_active or not institution.organization.is_active:
        return {"message": "institution not found"}, 404

    return {"item": public_branch_payload(institution)}, 200


@institutions_bp.get("/<int:institution_id>/packages")
@jwt_required()
def list_packages(institution_id: int):
    institution = db.session.get(Institution, institution_id)
    if institution is None or not institution.is_active or not institution.organization.is_active:
        return {"message": "institution not found"}, 404

    packages = (
        Package.query.filter_by(institution_id=institution_id, is_active=True)
        .filter(Package.current_version_id.is_not(None))
        .order_by(Package.id.asc())
        .all()
    )
    return {
        "institution": {
            "id": institution.id,
            "organization_id": institution.organization_id,
            "name": institution.organization.name,
            "branch_name": institution.branch_name,
        },
        "items": [public_package_payload(item) for item in packages],
    }, 200
