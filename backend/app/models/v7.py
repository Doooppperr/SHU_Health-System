from datetime import datetime, timezone

from app.extensions import db
from app.services.dates import calendar_date_iso


def utc_now():
    return datetime.now(timezone.utc)


class HealthDomain(db.Model):
    __tablename__ = "health_domains"
    __table_args__ = (
        db.CheckConstraint("length(trim(code)) > 0", name="ck_health_domains_code"),
        db.CheckConstraint("length(trim(name)) > 0", name="ck_health_domains_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), nullable=False, unique=True, index=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())

    def to_dict(self):
        return {"id": self.id, "code": self.code, "name": self.name,
                "description": self.description, "sort_order": self.sort_order,
                "is_active": self.is_active}


class IndicatorDomainLink(db.Model):
    __tablename__ = "indicator_domain_links"
    __table_args__ = (
        db.UniqueConstraint("indicator_dict_id", "health_domain_id", name="uq_indicator_domain_link"),
    )

    id = db.Column(db.Integer, primary_key=True)
    indicator_dict_id = db.Column(db.Integer, db.ForeignKey("indicator_dicts.id", ondelete="CASCADE"), nullable=False, index=True)
    health_domain_id = db.Column(db.Integer, db.ForeignKey("health_domains.id"), nullable=False, index=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    indicator = db.relationship("IndicatorDict", back_populates="domain_links")
    domain = db.relationship("HealthDomain")


class PackageVersion(db.Model):
    __tablename__ = "package_versions"
    __table_args__ = (db.UniqueConstraint("package_id", "version_number", name="uq_package_version"),)

    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.Integer, db.ForeignKey("packages.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = db.Column(db.Integer, nullable=False)
    package_type = db.Column(db.String(20), nullable=False, default="special")
    name_snapshot = db.Column(db.String(120), nullable=False)
    price_snapshot = db.Column(db.Numeric(10, 2), nullable=False)
    audience_snapshot = db.Column(db.String(120), nullable=True)
    description_snapshot = db.Column(db.Text, nullable=True)
    booking_notice_snapshot = db.Column(db.Text, nullable=True)
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    package = db.relationship("Package", back_populates="versions", foreign_keys=[package_id])
    domains = db.relationship("PackageVersionDomain", back_populates="version", cascade="all, delete-orphan", order_by="PackageVersionDomain.sort_order")

    def to_dict(self):
        return {"id": self.id, "package_id": self.package_id, "version_number": self.version_number,
                "package_type": self.package_type, "name": self.name_snapshot,
                "price": float(self.price_snapshot), "audience": self.audience_snapshot,
                "description": self.description_snapshot, "booking_notice": self.booking_notice_snapshot,
                "domains": [row.domain.to_dict() for row in self.domains if row.domain],
                "approved_at": self.approved_at.isoformat() if self.approved_at else None}


class PackageVersionDomain(db.Model):
    __tablename__ = "package_version_domains"
    __table_args__ = (db.UniqueConstraint("package_version_id", "health_domain_id", name="uq_package_version_domain"),)

    id = db.Column(db.Integer, primary_key=True)
    package_version_id = db.Column(db.Integer, db.ForeignKey("package_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    health_domain_id = db.Column(db.Integer, db.ForeignKey("health_domains.id"), nullable=False, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    version = db.relationship("PackageVersion", back_populates="domains")
    domain = db.relationship("HealthDomain")


class BookingGroup(db.Model):
    __tablename__ = "booking_groups"
    __table_args__ = (db.CheckConstraint("party_size between 1 and 5", name="ck_booking_groups_party_size"),)

    id = db.Column(db.Integer, primary_key=True)
    group_code = db.Column(db.String(36), nullable=False, unique=True, index=True)
    booked_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), nullable=False, index=True)
    package_id = db.Column(db.Integer, db.ForeignKey("packages.id", ondelete="SET NULL"), nullable=True)
    package_version_id = db.Column(db.Integer, db.ForeignKey("package_versions.id"), nullable=True, index=True)
    appointment_date = db.Column(db.Date, nullable=False, index=True)
    party_size = db.Column(db.Integer, nullable=False)
    package_name_snapshot = db.Column(db.String(120), nullable=False)
    package_price_snapshot = db.Column(db.Numeric(10, 2), nullable=False)
    domain_snapshot = db.Column(db.JSON, nullable=False, default=list)
    booking_notice_snapshot = db.Column(db.Text, nullable=True)
    notice_version_snapshot = db.Column(db.Integer, nullable=True)
    notice_confirmed_at = db.Column(db.DateTime(timezone=True), nullable=False)
    contact_snapshot = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    appointments = db.relationship("Appointment", back_populates="booking_group")
    institution = db.relationship("Institution")
    package_version = db.relationship("PackageVersion")

    def to_dict(self, include_appointments=True):
        data = {"id": self.id, "group_code": self.group_code, "booked_by_user_id": self.booked_by_user_id,
                "institution_id": self.institution_id, "package_id": self.package_id,
                "package_version_id": self.package_version_id, "appointment_date": calendar_date_iso(self.appointment_date),
                "party_size": self.party_size, "package_name": self.package_name_snapshot,
                "package_price": float(self.package_price_snapshot), "domains": self.domain_snapshot or [],
                "booking_notice": self.booking_notice_snapshot,
                "notice_confirmed_at": self.notice_confirmed_at.isoformat() if self.notice_confirmed_at else None,
                "created_at": self.created_at.isoformat() if self.created_at else None}
        if include_appointments:
            data["appointments"] = [row.to_dict(include_user=True) for row in self.appointments]
        return data


class AppointmentEvent(db.Model):
    __tablename__ = "appointment_events"
    __table_args__ = (db.UniqueConstraint("appointment_id", "event_type", "occurred_at", name="uq_appointment_event"),)

    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = db.Column(db.String(32), nullable=False)
    status_snapshot = db.Column(db.String(24), nullable=False)
    message = db.Column(db.String(255), nullable=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    appointment = db.relationship("Appointment", back_populates="events")

    def to_dict(self):
        return {"id": self.id, "type": self.event_type, "status": self.status_snapshot,
                "message": self.message, "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None}


class AppointmentCapacitySlot(db.Model):
    __tablename__ = "appointment_capacity_slots"
    __table_args__ = (db.UniqueConstraint("institution_id", "appointment_date", name="uq_capacity_slot_date"),)
    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    appointment_date = db.Column(db.Date, nullable=False, index=True)
    capacity = db.Column(db.Integer, nullable=True)
    revision = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class WaitlistSubscription(db.Model):
    __tablename__ = "waitlist_subscriptions"
    __table_args__ = (
        db.CheckConstraint("party_size between 1 and 5", name="ck_waitlist_party_size"),
        db.CheckConstraint("status in ('active','closed','cancelled','invalid')", name="ck_waitlist_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    subscriber_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), nullable=False, index=True)
    package_id = db.Column(db.Integer, db.ForeignKey("packages.id", ondelete="CASCADE"), nullable=False)
    package_version_id = db.Column(db.Integer, db.ForeignKey("package_versions.id"), nullable=True)
    appointment_date = db.Column(db.Date, nullable=False, index=True)
    party_size = db.Column(db.Integer, nullable=False)
    notification_email = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    last_satisfied_revision = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    closed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    participants = db.relationship("WaitlistSubscriptionParticipant", back_populates="subscription", cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "institution_id": self.institution_id, "package_id": self.package_id,
                "appointment_date": calendar_date_iso(self.appointment_date), "party_size": self.party_size,
                "notification_email": self.notification_email, "status": self.status,
                "participants": [row.to_dict() for row in self.participants],
                "created_at": self.created_at.isoformat() if self.created_at else None}


class WaitlistSubscriptionParticipant(db.Model):
    __tablename__ = "waitlist_subscription_participants"
    __table_args__ = (db.UniqueConstraint("subscription_id", "subject_user_id", name="uq_waitlist_participant"),)
    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey("waitlist_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name_snapshot = db.Column(db.String(80), nullable=False)
    health_id_snapshot = db.Column(db.String(20), nullable=False)
    booking_authorized_at = db.Column(db.DateTime(timezone=True), nullable=True)
    subscription = db.relationship("WaitlistSubscription", back_populates="participants")
    def to_dict(self):
        return {"id": self.id, "subject_user_id": self.subject_user_id, "name": self.name_snapshot,
                "booking_authorized_at": self.booking_authorized_at.isoformat() if self.booking_authorized_at else None}


class AvailabilityNotificationEvent(db.Model):
    __tablename__ = "availability_notification_events"
    __table_args__ = (db.UniqueConstraint("subscription_id", "capacity_revision", name="uq_availability_event_round"),)
    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey("waitlist_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    capacity_revision = db.Column(db.Integer, nullable=False)
    remaining_snapshot = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class NotificationOutbox(db.Model):
    __tablename__ = "notification_outbox"
    __table_args__ = (db.CheckConstraint("status in ('pending','sending','sent','failed')", name="ck_outbox_status"),)
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(40), nullable=False, index=True)
    idempotency_key = db.Column(db.String(160), nullable=False, unique=True, index=True)
    recipient = db.Column(db.String(120), nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    next_attempt_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)


class NotificationDelivery(db.Model):
    __tablename__ = "notification_deliveries"
    id = db.Column(db.Integer, primary_key=True)
    outbox_id = db.Column(db.Integer, db.ForeignKey("notification_outbox.id", ondelete="CASCADE"), nullable=False, index=True)
    success = db.Column(db.Boolean, nullable=False)
    provider_message_id = db.Column(db.String(160), nullable=True)
    error_message = db.Column(db.String(500), nullable=True)
    attempted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class ReportTextResult(db.Model):
    __tablename__ = "report_text_results"
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("institution_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    health_domain_id = db.Column(db.Integer, db.ForeignKey("health_domains.id"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    source_snapshot = db.Column(db.String(120), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    domain = db.relationship("HealthDomain")
    report = db.relationship("InstitutionReport", back_populates="text_results")
    def to_dict(self):
        return {"id": self.id, "domain_id": self.health_domain_id, "title": self.title,
                "body": self.body, "source": self.source_snapshot, "sort_order": self.sort_order}


class ReportAsset(db.Model):
    __tablename__ = "report_assets"
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("institution_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    health_domain_id = db.Column(db.Integer, db.ForeignKey("health_domains.id"), nullable=False, index=True)
    modality = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    storage_key = db.Column(db.String(255), nullable=False, unique=True)
    mime_type = db.Column(db.String(80), nullable=False)
    byte_size = db.Column(db.Integer, nullable=False)
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    page_count = db.Column(db.Integer, nullable=True)
    sha256 = db.Column(db.String(64), nullable=False)
    annotation_text = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    domain = db.relationship("HealthDomain")
    report = db.relationship("InstitutionReport", back_populates="assets")
    def to_dict(self, health_data_id=None):
        data = {"id": self.id, "domain_id": self.health_domain_id, "modality": self.modality,
                "title": self.title, "mime_type": self.mime_type, "byte_size": self.byte_size,
                "width": self.width, "height": self.height, "page_count": self.page_count,
                "sha256": self.sha256, "annotation": self.annotation_text, "sort_order": self.sort_order}
        if health_data_id:
            data["content_url"] = f"/api/health-data/{health_data_id}/assets/{self.id}/content"
        return data


class ReportAssetAnnotation(db.Model):
    __tablename__ = "report_asset_annotations"
    id = db.Column(db.Integer, primary_key=True)
    report_asset_id = db.Column(db.Integer, db.ForeignKey("report_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    annotation_type = db.Column(db.String(30), nullable=False)
    geometry = db.Column(db.JSON, nullable=True)
    text = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
