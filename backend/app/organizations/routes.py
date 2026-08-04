from flask import request
from flask_jwt_extended import jwt_required
from sqlalchemy import and_, or_

from app.extensions import db
from app.models import Institution, Organization
from app.organizations import organizations_bp
from app.public_api.routes import public_branch_payload


def _escaped_like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _contains(value, term: str) -> bool:
    return term.casefold() in str(value or "").casefold()


def _branch_matches(branch, term: str) -> bool:
    return any(
        _contains(branch.get(field), term)
        for field in ("branch_name", "district", "address", "metro_info")
    )


@organizations_bp.get("")
@jwt_required()
def list_organizations():
    term = (request.args.get("q") or "").strip()[:80]
    query = Organization.query.filter_by(is_active=True)
    if term:
        pattern = _escaped_like_pattern(term)
        branch_match = and_(
            Institution.is_active.is_(True),
            Institution.operations_suspended_at.is_(None),
            or_(
                Institution.branch_name.ilike(pattern, escape="\\"),
                Institution.district.ilike(pattern, escape="\\"),
                Institution.address.ilike(pattern, escape="\\"),
                Institution.metro_info.ilike(pattern, escape="\\"),
            ),
        )
        query = query.filter(
            or_(
                Organization.name.ilike(pattern, escape="\\"),
                Organization.description.ilike(pattern, escape="\\"),
                Organization.branches.any(branch_match),
            )
        )

    rows = query.order_by(Organization.id).all()
    items = []
    for row in rows:
        branches = [
            public_branch_payload(branch)
            for branch in row.branches
            if branch.is_active
        ]
        payload = {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "service_features": list(row.service_features or []),
            "branch_count": len(branches),
            "active_branch_count": len(branches),
            "branches": branches,
        }
        organization_matches = _contains(row.name, term) or _contains(row.description, term)
        if term and not organization_matches:
            branches = [branch for branch in branches if _branch_matches(branch, term)]
        if term and not branches:
            continue
        payload["branches"] = branches
        payload["active_branch_count"] = len(payload["branches"])
        items.append(payload)
    return {"items": items}, 200


@organizations_bp.get("/<int:organization_id>")
@jwt_required()
def get_organization(organization_id):
    row = db.session.get(Organization, organization_id)
    if row is None or not row.is_active:
        return {"message": "organization not found"}, 404
    branches = [
        public_branch_payload(branch)
        for branch in row.branches
        if branch.is_active
    ]
    payload = {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "service_features": list(row.service_features or []),
        "branch_count": len(branches),
        "active_branch_count": len(branches),
        "branches": branches,
    }
    return {"item": payload}, 200
