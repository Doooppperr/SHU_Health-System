from flask import request
from sqlalchemy import and_, or_

from app.extensions import db
from app.models import Comment, Institution, Organization, Package
from app.public_api import public_bp
from app.services.platform_contact import platform_contact_payload
from app.services.catalog_search import normalize_search_mode, run_catalog_search


def _image_payload(image):
    return {
        "id": image.id,
        "image_url": image.image_url,
        "sort_order": image.sort_order,
        "is_cover": image.sort_order == 0,
    }


def public_branch_payload(branch):
    images = [_image_payload(row) for row in branch.images]
    cover = images[0]["image_url"] if images else branch.logo_url
    return {
        "id": branch.id,
        "organization_id": branch.organization_id,
        "name": branch.organization.name if branch.organization else branch.name,
        "branch_name": branch.branch_name,
        "address": branch.address,
        "district": branch.district,
        "metro_info": branch.metro_info,
        "consult_phone": branch.consult_phone,
        "ext": branch.ext,
        "closed_day": branch.closed_day,
        "description": branch.description,
        "cover_image_url": cover,
        "logo_url": cover,
        "images": images,
        "package_count": sum(
            1
            for package in branch.packages
            if package.is_active and package.current_version_id is not None
        ),
    }


def public_package_payload(package):
    current = next(
        (row for row in package.versions if row.id == package.current_version_id),
        None,
    )
    domains = [
        {
            "id": link.domain.id,
            "code": link.domain.code,
            "name": link.domain.name,
            "description": link.domain.description,
        }
        for link in (current.domains if current else [])
        if link.domain and link.domain.is_active
    ]
    return {
        "id": package.id,
        "institution_id": package.institution_id,
        "name": package.name,
        "focus_area": package.focus_area,
        "gender_scope": package.gender_scope,
        "price": float(package.price),
        "description": package.description,
        "package_type": package.package_type,
        "audience": package.audience,
        "booking_notice": package.booking_notice,
        "version_number": current.version_number if current else None,
        "domains": domains,
    }


def public_comment_payload(comment):
    display_source = str(
        (comment.user.real_name if comment.user else None) or ""
    ).strip()
    display_name = f"{display_source[0]}***" if display_source else "平台用户"
    reply = comment.reply if comment.reply and comment.reply.status == "approved" else None
    return {
        "id": comment.id,
        "institution_id": comment.institution_id,
        "content": comment.content,
        "rating": comment.rating,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
        "author_display_name": display_name,
        "reply": {
            "content": reply.content,
            "submitted_at": (
                reply.submitted_at.isoformat() if reply.submitted_at else None
            ),
        } if reply else None,
    }


def _like(term):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def public_organization_search_items(outcome):
    items = []
    for organization_match in outcome["matches"]:
        row = organization_match["organization"]
        branches = []
        matched_packages = []
        seen_package_ids = set()
        for branch_match in organization_match["branches"]:
            branch = public_branch_payload(branch_match["branch"])
            branch["match_reasons"] = list(branch_match["reasons"])
            branch["matched_packages"] = [
                dict(package_match["public"])
                for package_match in branch_match["matched_packages"]
            ]
            for package in branch["matched_packages"]:
                if package["id"] not in seen_package_ids:
                    seen_package_ids.add(package["id"])
                    matched_packages.append(package)
            branches.append(branch)
        if branches:
            items.append({
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "service_features": list(row.service_features or []),
                "branch_count": len(branches),
                "active_branch_count": len(branches),
                "match_reasons": list(organization_match["reasons"]),
                "matched_packages": matched_packages,
                "branches": branches,
            })
    return items


@public_bp.get("/contact")
def contact():
    return {"item": platform_contact_payload()}, 200


@public_bp.get("/organizations")
def organizations():
    term = str(request.args.get("q") or "").strip()[:80]
    search_mode = normalize_search_mode(request.args.get("search_mode"))
    if search_mode:
        outcome = run_catalog_search(term, mode=search_mode)
        return {
            "items": public_organization_search_items(outcome),
            "search": outcome["search"],
            "platform_contact": platform_contact_payload(),
        }, 200
    query = Organization.query.filter_by(is_active=True)
    if term:
        pattern = _like(term)
        query = query.filter(or_(
            Organization.name.ilike(pattern, escape="\\"),
            Organization.description.ilike(pattern, escape="\\"),
            Organization.branches.any(and_(
                Institution.is_active.is_(True),
                Institution.operations_suspended_at.is_(None),
                or_(
                    Institution.branch_name.ilike(pattern, escape="\\"),
                    Institution.district.ilike(pattern, escape="\\"),
                    Institution.address.ilike(pattern, escape="\\"),
                    Institution.metro_info.ilike(pattern, escape="\\"),
                ),
            )),
        ))
    items = []
    for row in query.order_by(Organization.id).all():
        branches = [
            public_branch_payload(branch)
            for branch in row.branches
            if branch.is_active and branch.operations_suspended_at is None
        ]
        if term:
            folded = term.casefold()
            organization_match = folded in row.name.casefold() or folded in str(row.description or "").casefold()
            if not organization_match:
                branches = [
                    branch for branch in branches
                    if any(
                        folded in str(branch.get(field) or "").casefold()
                        for field in ("branch_name", "district", "address", "metro_info")
                    )
                ]
        if branches:
            items.append({
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "service_features": list(row.service_features or []),
                "branch_count": len(branches),
                "branches": branches,
            })
    return {"items": items, "platform_contact": platform_contact_payload()}, 200


def _active_branch(institution_id):
    return Institution.query.join(Institution.organization).filter(
        Institution.id == institution_id,
        Institution.is_active.is_(True),
        Institution.operations_suspended_at.is_(None),
        Organization.is_active.is_(True),
    ).first()


@public_bp.get("/institutions/<int:institution_id>")
def institution_detail(institution_id):
    branch = _active_branch(institution_id)
    if branch is None:
        return {"message": "没有找到该体检分院"}, 404
    payload = public_branch_payload(branch)
    payload["organization"] = {
        "id": branch.organization.id,
        "name": branch.organization.name,
        "description": branch.organization.description,
        "service_features": list(branch.organization.service_features or []),
    }
    return {"item": payload, "platform_contact": platform_contact_payload()}, 200


@public_bp.get("/institutions/<int:institution_id>/packages")
def institution_packages(institution_id):
    branch = _active_branch(institution_id)
    if branch is None:
        return {"message": "没有找到该体检分院"}, 404
    rows = Package.query.filter_by(
        institution_id=branch.id,
        is_active=True,
    ).filter(
        Package.current_version_id.is_not(None),
    ).order_by(Package.id).all()
    return {
        "institution": public_branch_payload(branch),
        "items": [public_package_payload(row) for row in rows],
    }, 200


@public_bp.get("/institutions/<int:institution_id>/comments")
def institution_comments(institution_id):
    branch = _active_branch(institution_id)
    if branch is None:
        return {"message": "没有找到该体检分院"}, 404
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", 10, type=int) or 10, 1), 50)
    query = Comment.query.filter_by(
        institution_id=branch.id,
        is_visible=True,
    ).order_by(Comment.created_at.desc(), Comment.id.desc())
    total = query.count()
    rows = query.offset((page - 1) * size).limit(size).all()
    return {
        "items": [public_comment_payload(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200
