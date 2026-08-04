from datetime import datetime, timezone

import bcrypt
from sqlalchemy.orm import synonym

from app.extensions import db
from app.services.account_email import effective_account_email


def utc_now():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (
        db.UniqueConstraint(
            "managed_institution_id",
            name="uq_users_managed_institution",
        ),
        db.CheckConstraint(
            "role in ('user', 'institution_admin', 'admin')",
            name="ck_users_role",
        ),
        db.CheckConstraint(
            "(role = 'institution_admin' and "
            "(managed_institution_id is not null or is_active = false)) "
            "or (role in ('user', 'admin') and managed_institution_id is null)",
            name="ck_users_role_institution_binding",
        ),
        db.CheckConstraint(
            "(role = 'user' and health_id is not null) or "
            "(role in ('institution_admin', 'admin') and health_id is null)",
            name="ck_users_role_health_identity",
        ),
        db.CheckConstraint(
            "gender is null or gender in ('male', 'female', 'other', 'undisclosed')",
            name="ck_users_gender",
        ),
        db.CheckConstraint("length(trim(username)) > 0", name="ck_users_username_not_blank"),
    )

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # Email is a notification destination, not an account identifier. Families
    # and local demonstrations may intentionally share one mailbox.
    email = db.Column(db.String(120), nullable=True, index=True)
    email_verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    role = db.Column(db.String(20), default="user", nullable=False)
    managed_institution_id = db.Column(
        db.Integer,
        db.ForeignKey("institutions.id"),
        nullable=True,
        index=True,
    )
    health_id = db.Column(db.String(20), unique=True, nullable=True, index=True)
    real_name = db.Column(db.String(80), nullable=True)
    birth_date = db.Column(db.Date, nullable=True)
    gender = db.Column(db.String(20), nullable=True)
    identity_completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Rolling API compatibility for v11 clients and older application code.
    # The persisted v12 column is ``identity_completed_at``.
    profile_completed_at = synonym("identity_completed_at")
    allow_health_id_proxy_booking = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
    )
    # API/model compatibility for clients built during the v12 development
    # window. The persisted column uses the finalized product language.
    health_id_booking_enabled = synonym("allow_health_id_proxy_booking")
    booking_authorization_version = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    allergy_history = db.Column(db.Text, nullable=True)
    medical_history = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    must_change_initial_password = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    token_version = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    managed_institution = db.relationship(
        "Institution",
        back_populates="administrators",
        foreign_keys=[managed_institution_id],
    )
    issued_institution_invites = db.relationship(
        "InstitutionInvite",
        back_populates="issued_by_admin",
        foreign_keys="InstitutionInvite.issued_by_admin_id",
    )
    used_institution_invites = db.relationship(
        "InstitutionInvite",
        back_populates="used_by_user",
        foreign_keys="InstitutionInvite.used_by_user_id",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(password.encode("utf-8"), self.password_hash.encode("utf-8"))

    @property
    def profile_completed(self) -> bool:
        """Only an explicitly confirmed or migrated identity is usable."""
        return bool(
            self.role == "user"
            and self.identity_completed_at is not None
            and (self.real_name or "").strip()
            and self.birth_date is not None
            and self.gender in {"male", "female", "other", "undisclosed"}
        )

    def to_dict(self, *, include_profile: bool = True) -> dict:
        institution = None
        if self.managed_institution is not None:
            institution = {
                "id": self.managed_institution.id,
                "organization_id": self.managed_institution.organization_id,
                "name": self.managed_institution.organization.name if self.managed_institution.organization else self.managed_institution.name,
                "branch_name": self.managed_institution.branch_name,
                "is_active": self.managed_institution.is_active,
            }
        result = {
            "id": self.id,
            "username": self.username,
            "email": effective_account_email(self),
            "email_verified_at": self.email_verified_at.isoformat() if self.email_verified_at else None,
            "phone": self.phone,
            "role": self.role,
            "is_active": self.is_active,
            "must_change_initial_password": self.must_change_initial_password,
            "managed_institution_id": self.managed_institution_id,
            "managed_institution": institution,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_profile and self.role == "user":
            result.update(
                health_id=self.health_id,
                real_name=self.real_name,
                birth_date=self.birth_date.isoformat() if self.birth_date else None,
                gender=self.gender,
                identity_completed=self.profile_completed,
                identity_completed_at=(
                    self.identity_completed_at.isoformat()
                    if self.identity_completed_at
                    else None
                ),
                profile_completed=self.profile_completed,
                profile_completed_at=(
                    self.identity_completed_at.isoformat()
                    if self.identity_completed_at
                    else None
                ),
                allow_health_id_proxy_booking=self.allow_health_id_proxy_booking,
                health_id_booking_enabled=self.allow_health_id_proxy_booking,
                allergy_history=self.allergy_history,
                medical_history=self.medical_history,
            )
        return result

    def friend_identity_dict(self) -> dict:
        """Return the display identity allowed in health-owner selectors."""
        return {"id": self.id, "display_name": self.real_name or "未完善姓名"}
