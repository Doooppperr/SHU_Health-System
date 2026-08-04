from datetime import datetime, timezone

from app.extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class CommentSanction(db.Model):
    __tablename__ = "comment_sanctions"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('active','lifted','expired')",
            name="ck_comment_sanctions_status",
        ),
        db.CheckConstraint(
            "duration_days is null or duration_days in (7,30)",
            name="ck_comment_sanctions_duration",
        ),
        db.CheckConstraint("length(trim(reason)) > 0", name="ck_comment_sanctions_reason"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_comment_id = db.Column(
        db.Integer,
        db.ForeignKey("comments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reason = db.Column(db.String(500), nullable=False)
    duration_days = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_by_admin_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    lifted_by_admin_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    lifted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    lift_reason = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    user = db.relationship("User", foreign_keys=[user_id])
    source_comment = db.relationship("Comment")
    creator = db.relationship("User", foreign_keys=[created_by_admin_id])
    lifter = db.relationship("User", foreign_keys=[lifted_by_admin_id])
    appeal = db.relationship(
        "CommentAppeal",
        back_populates="sanction",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user": {
                "id": self.user.id,
                "username": self.user.username,
            } if self.user else None,
            "source_comment_id": self.source_comment_id,
            "reason": self.reason,
            "duration_days": self.duration_days,
            "duration_label": "永久" if self.duration_days is None else f"{self.duration_days}天",
            "status": self.status,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "lifted_at": self.lifted_at.isoformat() if self.lifted_at else None,
            "lift_reason": self.lift_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "appeal": self.appeal.to_dict() if self.appeal else None,
        }


class CommentAppeal(db.Model):
    __tablename__ = "comment_appeals"
    __table_args__ = (
        db.UniqueConstraint("sanction_id", name="uq_comment_appeals_sanction"),
        db.CheckConstraint(
            "status in ('pending','approved','rejected')",
            name="ck_comment_appeals_status",
        ),
        db.CheckConstraint("length(trim(content)) > 0", name="ck_comment_appeals_content"),
    )

    id = db.Column(db.Integer, primary_key=True)
    sanction_id = db.Column(
        db.Integer,
        db.ForeignKey("comment_sanctions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    review_note = db.Column(db.String(500), nullable=True)
    reviewed_by_admin_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    sanction = db.relationship("CommentSanction", back_populates="appeal")
    user = db.relationship("User", foreign_keys=[user_id])
    reviewer = db.relationship("User", foreign_keys=[reviewed_by_admin_id])

    def to_dict(self):
        return {
            "id": self.id,
            "sanction_id": self.sanction_id,
            "user_id": self.user_id,
            "user": {
                "id": self.user.id,
                "username": self.user.username,
            } if self.user else None,
            "content": self.content,
            "status": self.status,
            "review_note": self.review_note,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
        }
