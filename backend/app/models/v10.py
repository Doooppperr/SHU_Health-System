from datetime import datetime, timezone

from app.extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class UserNotification(db.Model):
    __tablename__ = "user_notifications"
    __table_args__ = (
        db.UniqueConstraint("user_id", "idempotency_key", name="uq_user_notifications_user_key"),
        db.CheckConstraint("length(trim(title)) > 0", name="ck_user_notifications_title"),
        db.CheckConstraint("length(trim(body)) > 0", name="ck_user_notifications_body"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    idempotency_key = db.Column(db.String(180), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    action_url = db.Column(db.String(500), nullable=True)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    read_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "event_type": self.event_type,
            "title": self.title,
            "body": self.body,
            "action_url": self.action_url,
            "payload": self.payload or {},
            "is_read": self.read_at is not None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ReportAssetType(db.Model):
    __tablename__ = "report_asset_types"
    __table_args__ = (
        db.UniqueConstraint("code", name="uq_report_asset_types_code"),
        db.CheckConstraint("max_files between 1 and 2", name="ck_report_asset_types_max_files"),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False, index=True)
    health_domain_id = db.Column(db.Integer, db.ForeignKey("health_domains.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    modality = db.Column(db.String(40), nullable=False, default="image", server_default="image")
    max_files = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    sort_order = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())

    domain = db.relationship("HealthDomain")

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "domain_id": self.health_domain_id,
            "name": self.name,
            "description": self.description,
            "modality": self.modality,
            "max_files": self.max_files,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
        }


class PackageVersionAssetRequirement(db.Model):
    __tablename__ = "package_version_asset_requirements"
    __table_args__ = (
        db.UniqueConstraint(
            "package_version_id",
            "asset_type_id",
            name="uq_package_version_asset_requirement",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    package_version_id = db.Column(
        db.Integer,
        db.ForeignKey("package_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_type_id = db.Column(
        db.Integer,
        db.ForeignKey("report_asset_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_required = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    sort_order = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    asset_type = db.relationship("ReportAssetType")

    def to_dict(self):
        return {
            "id": self.id,
            "package_version_id": self.package_version_id,
            "asset_type_id": self.asset_type_id,
            "is_required": self.is_required,
            "sort_order": self.sort_order,
            "asset_type": self.asset_type.to_dict() if self.asset_type else None,
        }
