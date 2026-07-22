from datetime import datetime, timezone
from uuid import uuid4

import bcrypt

from app.extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class PasswordVerificationChallenge(db.Model):
    __tablename__ = "password_verification_challenges"
    __table_args__ = (
        db.CheckConstraint("purpose in ('reset','change')", name="ck_password_challenge_purpose"),
        db.CheckConstraint("attempt_count between 0 and 5", name="ck_password_challenge_attempts"),
    )

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, index=True, default=lambda: str(uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    purpose = db.Column(db.String(16), nullable=False, index=True)
    email_snapshot = db.Column(db.String(120), nullable=False)
    code_hash = db.Column(db.String(255), nullable=False)
    request_ip_hash = db.Column(db.String(64), nullable=True, index=True)
    attempt_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)

    user = db.relationship("User")

    def set_code(self, code: str) -> None:
        self.code_hash = bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def check_code(self, code: str) -> bool:
        return bcrypt.checkpw(code.encode("utf-8"), self.code_hash.encode("utf-8"))
