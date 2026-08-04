from __future__ import annotations

from app.extensions import db
from app.models import (
    HealthDomain, Institution, Package, PackageChangeRequest, PackageVersion, PackageVersionDomain,
    PackageVersionAssetRequirement, ReportAssetType,
)
from app.services.institution_management import ManagementValidationError, apply_package_payload


def _claim_package_change_slot(package):
    """Serialize pending-review creation for one existing package.

    The no-op UPDATE is intentional.  It takes a database write/row lock until
    the caller commits, including on SQLite where ``FOR UPDATE`` is ignored.
    A waiter therefore checks for an existing pending request only after the
    winning transaction has committed it.
    """
    claimed = Package.query.filter(
        Package.id == package.id,
        Package.institution_id == package.institution_id,
    ).update({Package.is_active: Package.is_active}, synchronize_session=False)
    if claimed != 1:
        raise ManagementValidationError("package no longer exists")


def _claim_institution_package_creation_slot(institution):
    """Serialize pending package creations within one institution.

    New-package requests do not have a ``package_id`` that can be protected by
    the package-level slot.  Claiming the owning institution makes the
    same-name lookup and insert one critical section on SQLite and openGauss.
    """
    claimed = Institution.query.filter(
        Institution.id == institution.id,
    ).update(
        {Institution.is_active: Institution.is_active},
        synchronize_session=False,
    )
    if claimed != 1:
        raise ManagementValidationError("institution no longer exists")


def _package_data(package):
    current = next((row for row in package.versions if row.id == package.current_version_id), None)
    return {
        "name": package.name,
        "focus_area": package.focus_area,
        "gender_scope": package.gender_scope,
        "price": float(package.price),
        "description": package.description,
        "package_type": package.package_type,
        "audience": package.audience,
        "booking_notice": package.booking_notice,
        "domain_ids": [row.health_domain_id for row in current.domains] if current else [],
        "required_asset_type_ids": [
            row.asset_type_id for row in current.asset_requirements if row.is_required
        ] if current else [],
        "is_active": package.is_active,
    }


def _normalized_package_data(institution_id, payload, *, base=None):
    source = _package_data(base) if base is not None else {}
    source.update(payload or {})
    candidate = Package(institution_id=institution_id)
    apply_package_payload(candidate, source, creating=base is None)
    if not candidate.package_type:
        candidate.package_type = "special"
    if base is None and candidate.is_active is None:
        candidate.is_active = True
    result = _package_data(candidate)
    raw_domain_ids = source.get("domain_ids")
    if raw_domain_ids is None:
        fallback = HealthDomain.query.filter_by(code="other", is_active=True).first()
        raw_domain_ids = [fallback.id] if fallback else []
    if not isinstance(raw_domain_ids, list):
        raise ManagementValidationError("domain_ids must be an array")
    try: domain_ids = list(dict.fromkeys(int(value) for value in raw_domain_ids))
    except (TypeError, ValueError): raise ManagementValidationError("domain_ids must contain integers") from None
    if candidate.package_type == "special" and len(domain_ids) != 1:
        raise ManagementValidationError("专项套餐必须恰好选择一个健康领域")
    if candidate.package_type == "combined" and len(domain_ids) < 2:
        raise ManagementValidationError("组合套餐必须至少选择两个健康领域")
    if len(domain_ids) > 8 or HealthDomain.query.filter(HealthDomain.id.in_(domain_ids), HealthDomain.is_active.is_(True)).count() != len(domain_ids):
        raise ManagementValidationError("domain_ids contains an invalid health domain")
    result["domain_ids"] = domain_ids
    raw_asset_type_ids = source.get("required_asset_type_ids") or []
    if not isinstance(raw_asset_type_ids, list):
        raise ManagementValidationError("required_asset_type_ids must be an array")
    try:
        asset_type_ids = list(dict.fromkeys(int(value) for value in raw_asset_type_ids))
    except (TypeError, ValueError):
        raise ManagementValidationError("required_asset_type_ids must contain integers") from None
    asset_types = ReportAssetType.query.filter(
        ReportAssetType.id.in_(asset_type_ids),
        ReportAssetType.is_active.is_(True),
    ).all() if asset_type_ids else []
    if len(asset_types) != len(asset_type_ids):
        raise ManagementValidationError("required_asset_type_ids contains an invalid attachment type")
    if any(asset_type.health_domain_id not in domain_ids for asset_type in asset_types):
        raise ManagementValidationError("required attachment types must belong to a selected health domain")
    result["required_asset_type_ids"] = asset_type_ids
    return result


def create_change_request(institution, requester, action, *, package=None, payload=None):
    if action not in {"create", "update", "deactivate", "reactivate"}:
        raise ManagementValidationError("invalid package change action")
    if action != "create" and package is None:
        raise ManagementValidationError("package is required")
    if package is not None:
        _claim_package_change_slot(package)
        pending = PackageChangeRequest.query.filter_by(package_id=package.id, status="pending").first()
        if pending:
            raise ManagementValidationError("该套餐已有待审核申请，请先撤回或等待审核")
    else:
        _claim_institution_package_creation_slot(institution)

    before_data = _package_data(package) if package is not None else None
    proposed_data = None
    if action == "create":
        proposed_data = _normalized_package_data(institution.id, payload, base=None)
    elif action == "update":
        proposed_data = _normalized_package_data(institution.id, payload, base=package)
    elif action == "deactivate":
        if not package.is_active:
            raise ManagementValidationError("package is already inactive")
        proposed_data = {**before_data, "is_active": False}
    elif action == "reactivate":
        if package.is_active:
            raise ManagementValidationError("package is already active")
        proposed_data = {**before_data, "is_active": True}

    requested_name = (proposed_data or {}).get("name")
    if action == "create" and requested_name:
        existing = Package.query.filter_by(institution_id=institution.id, name=requested_name).first()
        if existing:
            raise ManagementValidationError("package name already exists")
        for pending in PackageChangeRequest.query.filter_by(institution_id=institution.id, action="create", status="pending").all():
            if (pending.proposed_data or {}).get("name") == requested_name:
                raise ManagementValidationError("同名套餐已有待审核申请")

    item = PackageChangeRequest(
        institution_id=institution.id,
        package_id=package.id if package else None,
        action=action,
        status="pending",
        before_data=before_data,
        proposed_data=proposed_data,
        requested_by_user_id=requester.id,
    )
    db.session.add(item)
    return item


def approve_change_request(item, reviewer, note=None):
    if item.status != "pending":
        raise ManagementValidationError("only pending requests can be reviewed")
    if item.action == "create":
        package = Package(institution_id=item.institution_id)
        apply_package_payload(package, item.proposed_data or {}, creating=True)
        package.is_active = bool((item.proposed_data or {}).get("is_active", True))
        db.session.add(package)
        db.session.flush()
        item.package_id = package.id
    else:
        package = db.session.get(Package, item.package_id)
        if package is None or package.institution_id != item.institution_id:
            raise ManagementValidationError("package no longer exists")
        if item.action == "update":
            apply_package_payload(package, item.proposed_data or {})
        elif item.action == "deactivate":
            package.is_active = False
        elif item.action == "reactivate":
            package.is_active = True
    if item.action in {"create", "update", "reactivate"}:
        proposed = item.proposed_data or {}
        latest = PackageVersion.query.filter_by(package_id=package.id).order_by(PackageVersion.version_number.desc()).first()
        version = PackageVersion(
            package_id=package.id, version_number=(latest.version_number + 1) if latest else 1,
            package_type=package.package_type, name_snapshot=package.name,
            price_snapshot=package.price, audience_snapshot=package.audience or package.gender_scope,
            description_snapshot=package.description, booking_notice_snapshot=package.booking_notice,
            approved_by_user_id=reviewer.id,
        )
        db.session.add(version); db.session.flush()
        for order, domain_id in enumerate(proposed.get("domain_ids") or []):
            db.session.add(PackageVersionDomain(package_version_id=version.id,
                                                health_domain_id=domain_id, sort_order=order))
        for order, asset_type_id in enumerate(proposed.get("required_asset_type_ids") or []):
            db.session.add(PackageVersionAssetRequirement(
                package_version_id=version.id,
                asset_type_id=asset_type_id,
                is_required=True,
                sort_order=order,
            ))
        package.current_version_id = version.id
    item.status = "approved"
    item.reviewed_by_user_id = reviewer.id
    item.review_note = str(note or "").strip() or None
    return item
