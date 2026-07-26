from datetime import datetime, timezone

from app.extensions import db


class FriendRelation(db.Model):
    __tablename__ = "friend_relations"
    __table_args__ = (
        db.UniqueConstraint("user_id", "friend_user_id", name="uq_friend_pair"),
        db.CheckConstraint("user_id <> friend_user_id", name="ck_friend_not_self"),
        db.CheckConstraint("length(trim(relation_name)) > 0", name="ck_friend_relation_name_not_blank"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    friend_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    relation_name = db.Column(db.String(80), nullable=False, default="亲友")
    auth_status = db.Column(db.Boolean, nullable=False, default=False)
    booking_auth_status = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    booking_authorized_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", foreign_keys=[user_id])
    friend_user = db.relationship("User", foreign_keys=[friend_user_id])

    @staticmethod
    def _masked_name(user):
        name = (user.real_name or "").strip()
        if not name:
            return "未完善姓名"
        if len(name) == 1:
            return f"{name}*"
        return f"{name[0]}{'*' * max(1, len(name) - 1)}"

    @classmethod
    def _identity(cls, user, *, authorized):
        if user is None:
            return None
        return {
            "id": user.id,
            "display_name": (user.real_name or "未完善姓名") if authorized else cls._masked_name(user),
        }

    def to_dict(self, *, viewer_id=None):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "friend_user_id": self.friend_user_id,
            "relation_name": self.relation_name,
            "auth_status": self.auth_status,
            "booking_auth_status": self.booking_auth_status,
            "booking_authorized_at": self.booking_authorized_at.isoformat() if self.booking_authorized_at else None,
            "created_at": self.created_at.isoformat(),
            "user": self._identity(self.user, authorized=bool(self.auth_status)),
            "friend_user": self._identity(self.friend_user, authorized=bool(self.auth_status)),
        }
