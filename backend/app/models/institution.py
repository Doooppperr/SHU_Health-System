from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.services.dates import calendar_date_iso


class Institution(db.Model):
    __tablename__ = "institutions"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "branch_name", name="uq_institution_branch"),
        db.CheckConstraint("length(trim(name)) > 0", name="ck_institutions_name_not_blank"),
        db.CheckConstraint("length(trim(branch_name)) > 0", name="ck_institutions_branch_not_blank"),
        db.CheckConstraint("length(trim(address)) > 0", name="ck_institutions_address_not_blank"),
        db.CheckConstraint("length(trim(district)) > 0", name="ck_institutions_district_not_blank"),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    # Compatibility snapshot retained for old clients and migrations. New
    # business reads use organization.name as the canonical brand name.
    name = db.Column(db.String(120), nullable=False)
    branch_name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    district = db.Column(db.String(80), nullable=False)
    metro_info = db.Column(db.String(255), nullable=True)
    consult_phone = db.Column(db.String(30), nullable=True)
    ext = db.Column(db.String(20), nullable=True)
    closed_day = db.Column(db.String(20), nullable=True)
    description = db.Column(db.Text, nullable=True)
    logo_url = db.Column(db.String(255), nullable=True)
    daily_appointment_limit = db.Column(db.Integer, nullable=True)
    notification_email = db.Column(db.String(120), nullable=True)
    notification_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
    )

    packages = db.relationship("Package", back_populates="institution", cascade="all, delete-orphan")
    organization = db.relationship("Organization", back_populates="branches")
    administrators = db.relationship(
        "User",
        back_populates="managed_institution",
        foreign_keys="User.managed_institution_id",
    )
    invite = db.relationship(
        "InstitutionInvite",
        back_populates="institution",
        cascade="all, delete-orphan",
        uselist=False,
    )
    images = db.relationship(
        "InstitutionImage",
        back_populates="institution",
        cascade="all, delete-orphan",
        order_by="InstitutionImage.sort_order.asc()",
    )
    appointments = db.relationship("Appointment", back_populates="institution")
    package_change_requests = db.relationship(
        "PackageChangeRequest", back_populates="institution", cascade="all, delete-orphan"
    )

    @property
    def administrator(self):
        """Compatibility accessor for older presentation helpers."""
        return self.administrators[0] if self.administrators else None

    def to_dict(self):
        image_items = [image.to_dict() for image in self.images]
        cover_image_url = image_items[0]["image_url"] if image_items else self.logo_url
        active_package_count = sum(1 for package in self.packages if package.is_active)
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "organization": {
                "id": self.organization.id,
                "name": self.organization.name,
                "is_active": self.organization.is_active,
            } if self.organization else None,
            "name": self.organization.name if self.organization else self.name,
            "branch_name": self.branch_name,
            "address": self.address,
            "district": self.district,
            "metro_info": self.metro_info,
            "consult_phone": self.consult_phone,
            "ext": self.ext,
            "closed_day": self.closed_day,
            "description": self.description,
            # Keep logo_url as a compatibility alias while clients move to the
            # ordered image collection.  The first image is always the cover.
            "logo_url": cover_image_url,
            "cover_image_url": cover_image_url,
            "images": image_items,
            "is_active": self.is_active,
            "daily_appointment_limit": self.daily_appointment_limit,
            "notification_email": self.notification_email,
            "package_count": active_package_count,
            "total_package_count": len(self.packages),
        }


class Package(db.Model):
    __tablename__ = "packages"
    __table_args__ = (
        db.UniqueConstraint("institution_id", "name", name="uq_package_institution_name"),
        db.CheckConstraint("length(trim(name)) > 0", name="ck_packages_name_not_blank"),
        db.CheckConstraint("length(trim(focus_area)) > 0", name="ck_packages_focus_area_not_blank"),
        db.CheckConstraint("gender_scope in ('all', 'male', 'female', 'female_all')", name="ck_packages_gender_scope"),
        db.CheckConstraint("price >= 0", name="ck_packages_price_non_negative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    focus_area = db.Column(db.String(120), nullable=False)
    gender_scope = db.Column(db.String(40), nullable=False, default="all")
    price = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    description = db.Column(db.Text, nullable=True)
    package_type = db.Column(db.String(20), nullable=False, default="special", server_default="special")
    audience = db.Column(db.String(120), nullable=True)
    booking_notice = db.Column(db.Text, nullable=True)
    current_version_id = db.Column(db.Integer, nullable=True, index=True)
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
    )

    institution = db.relationship("Institution", back_populates="packages")
    appointments = db.relationship("Appointment", back_populates="package")
    change_requests = db.relationship("PackageChangeRequest", back_populates="package")
    versions = db.relationship("PackageVersion", back_populates="package", foreign_keys="PackageVersion.package_id", cascade="all, delete-orphan")

    def to_dict(self):
        payload = {
            "id": self.id,
            "institution_id": self.institution_id,
            "name": self.name,
            "focus_area": self.focus_area,
            "gender_scope": self.gender_scope,
            "price": float(self.price),
            "description": self.description,
            "package_type": self.package_type,
            "audience": self.audience,
            "booking_notice": self.booking_notice,
            "current_version_id": self.current_version_id,
            "is_active": self.is_active,
        }
        current = next((row for row in self.versions if row.id == self.current_version_id), None)
        payload["version"] = current.to_dict() if current else None
        payload["domains"] = payload["version"]["domains"] if current else []
        return payload


def utc_now():
    return datetime.now(timezone.utc)


class Appointment(db.Model):
    __tablename__ = "appointments"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('unfulfilled', 'awaiting_report', 'fulfilled', 'invalidated', 'cancelled')",
            name="ck_appointments_status",
        ),
        db.UniqueConstraint("user_id", "active_date_key", name="uq_appointments_user_active_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), nullable=False, index=True)
    package_id = db.Column(db.Integer, db.ForeignKey("packages.id", ondelete="SET NULL"), nullable=True, index=True)
    package_version_id = db.Column(db.Integer, db.ForeignKey("package_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    booking_group_id = db.Column(db.Integer, db.ForeignKey("booking_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    booked_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    appointment_date = db.Column(db.Date, nullable=False, index=True)
    active_date_key = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(24), nullable=False, default="unfulfilled", index=True)
    user_name_snapshot = db.Column(db.String(80), nullable=False)
    user_health_id_snapshot = db.Column(db.String(20), nullable=False)
    user_birth_date_snapshot = db.Column(db.Date, nullable=True)
    user_gender_snapshot = db.Column(db.String(20), nullable=True)
    user_contact_snapshot = db.Column(db.String(120), nullable=True)
    package_name_snapshot = db.Column(db.String(120), nullable=False)
    package_price_snapshot = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    cancelled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    attended_at = db.Column(db.DateTime(timezone=True), nullable=True)
    invalidated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    fulfilled_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])
    institution = db.relationship("Institution", back_populates="appointments")
    package = db.relationship("Package", back_populates="appointments")
    package_version = db.relationship("PackageVersion")
    report = db.relationship("InstitutionReport", back_populates="appointment", uselist=False)
    booking_group = db.relationship("BookingGroup", back_populates="appointments")
    events = db.relationship("AppointmentEvent", back_populates="appointment", cascade="all, delete-orphan", order_by="AppointmentEvent.occurred_at")

    def to_dict(self, *, include_user=False):
        payload = {
            "id": self.id,
            "institution_id": self.institution_id,
            "package_id": self.package_id,
            "package_version_id": self.package_version_id,
            "booking_group_id": self.booking_group_id,
            "booked_by_user_id": self.booked_by_user_id,
            "appointment_date": calendar_date_iso(self.appointment_date),
            "status": self.status,
            "package_name": self.package_name_snapshot,
            "package_price": float(self.package_price_snapshot),
            "institution": {
                "id": self.institution.id,
                "name": self.institution.name,
                "branch_name": self.institution.branch_name,
            } if self.institution else None,
            "report_id": self.report.id if self.report else None,
            "report_status": self.report.status if self.report else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
            "attended_at": self.attended_at.isoformat() if self.attended_at else None,
            "invalidated_at": self.invalidated_at.isoformat() if self.invalidated_at else None,
            "fulfilled_at": self.fulfilled_at.isoformat() if self.fulfilled_at else None,
        }
        if include_user:
            payload["user"] = {
                "id": self.user_id,
                "name": self.user_name_snapshot,
                "health_id": self.user_health_id_snapshot,
                "birth_date": self.user_birth_date_snapshot.isoformat() if self.user_birth_date_snapshot else None,
                "gender": self.user_gender_snapshot,
            }
        return payload


class PackageChangeRequest(db.Model):
    __tablename__ = "package_change_requests"
    __table_args__ = (
        db.CheckConstraint(
            "action in ('create', 'update', 'deactivate', 'reactivate')",
            name="ck_package_change_requests_action",
        ),
        db.CheckConstraint(
            "status in ('pending', 'approved', 'rejected', 'withdrawn')",
            name="ck_package_change_requests_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    package_id = db.Column(db.Integer, db.ForeignKey("packages.id", ondelete="SET NULL"), nullable=True, index=True)
    action = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    before_data = db.Column(db.JSON, nullable=True)
    proposed_data = db.Column(db.JSON, nullable=True)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    review_note = db.Column(db.String(500), nullable=True)
    requested_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    withdrawn_at = db.Column(db.DateTime(timezone=True), nullable=True)

    institution = db.relationship("Institution", back_populates="package_change_requests")
    package = db.relationship("Package", back_populates="change_requests")
    requester = db.relationship("User", foreign_keys=[requested_by_user_id])
    reviewer = db.relationship("User", foreign_keys=[reviewed_by_user_id])

    def to_dict(self):
        from app.models.v7 import HealthDomain

        def details(data):
            if not data:
                return None
            value = dict(data)
            ids = value.pop("domain_ids", []) or []
            domains = HealthDomain.query.filter(HealthDomain.id.in_(ids)).all() if ids else []
            by_id = {row.id: row.name for row in domains}
            value["health_domains"] = [by_id[item] for item in ids if item in by_id]
            value["package_type_label"] = {"special": "专项套餐", "combined": "组合套餐"}.get(value.get("package_type"), "体检套餐")
            value["gender_scope_label"] = {"all": "不限性别", "male": "男性", "female": "女性", "female_all": "女性人群"}.get(value.get("gender_scope"), "不限性别")
            value["status_label"] = "启用" if value.get("is_active", True) else "停用"
            return value

        return {
            "id": self.id,
            "institution_id": self.institution_id,
            "institution": {
                "id": self.institution.id,
                "name": self.institution.name,
                "branch_name": self.institution.branch_name,
            } if self.institution else None,
            "package_id": self.package_id,
            "package_name": (self.proposed_data or {}).get("name") or (self.before_data or {}).get("name"),
            "action": self.action,
            "action_label": {"create": "新增套餐", "update": "修改套餐", "deactivate": "下架套餐", "reactivate": "恢复套餐"}.get(self.action, "套餐变更"),
            "status": self.status,
            "status_label": {"pending": "待审核", "approved": "已通过", "rejected": "已驳回", "withdrawn": "已撤回"}.get(self.status, "处理中"),
            "before_details": details(self.before_data),
            "proposed_details": details(self.proposed_data),
            "requester": self.requester.username if self.requester else None,
            "reviewer": self.reviewer.username if self.reviewer else None,
            "review_note": self.review_note,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "withdrawn_at": self.withdrawn_at.isoformat() if self.withdrawn_at else None,
        }
