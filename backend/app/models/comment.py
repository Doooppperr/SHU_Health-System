from datetime import datetime, timezone

from app.extensions import db


class Comment(db.Model):
    __tablename__ = "comments"
    __table_args__ = (
        db.CheckConstraint("rating between 1 and 5", name="ck_comments_rating_range"),
        db.CheckConstraint("length(trim(content)) > 0", name="ck_comments_content_not_blank"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    is_visible = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User")
    institution = db.relationship("Institution")
    reply = db.relationship("CommentReply", back_populates="comment", uselist=False, cascade="all, delete-orphan")

    def to_dict(self, *, include_unapproved_reply=False):
        reply = self.reply if self.reply and (include_unapproved_reply or self.reply.status == "approved") else None
        return {
            "id": self.id,
            "user_id": self.user_id,
            "institution_id": self.institution_id,
            "content": self.content,
            "rating": self.rating,
            "is_visible": self.is_visible,
            "created_at": self.created_at.isoformat(),
            "user": {
                "id": self.user.id,
                "username": self.user.username,
            }
            if self.user
            else None,
            "institution": {
                "id": self.institution.id,
                "name": self.institution.name,
                "branch_name": self.institution.branch_name,
            }
            if self.institution
            else None,
            "reply": reply.to_dict() if reply else None,
        }


class CommentReply(db.Model):
    __tablename__ = "comment_replies"
    __table_args__ = (
        db.CheckConstraint("status in ('pending','approved','rejected')", name="ck_comment_replies_status"),
        db.CheckConstraint("length(trim(content)) > 0", name="ck_comment_replies_content_not_blank"),
        db.UniqueConstraint("comment_id", name="uq_comment_replies_comment"),
    )

    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey("comments.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    submitted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    review_note = db.Column(db.String(500), nullable=True)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    user_read_at = db.Column(db.DateTime(timezone=True), nullable=True)

    comment = db.relationship("Comment", back_populates="reply")
    institution = db.relationship("Institution")
    submitter = db.relationship("User", foreign_keys=[submitted_by_user_id])
    reviewer = db.relationship("User", foreign_keys=[reviewed_by_user_id])

    def to_dict(self):
        return {
            "id": self.id,
            "comment_id": self.comment_id,
            "content": self.content,
            "status": self.status,
            "status_label": {"pending": "待审核", "approved": "已通过", "rejected": "已驳回"}.get(self.status, "处理中"),
            "review_note": self.review_note,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "is_unread": self.status == "approved" and self.user_read_at is None,
        }
