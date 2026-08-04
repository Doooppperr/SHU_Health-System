from datetime import datetime, timezone

from app.extensions import db


class FriendRelation(db.Model):
    __tablename__ = "friend_relations"
    __table_args__ = (
        db.UniqueConstraint("user_id", "friend_user_id", name="uq_friend_pair"),
        db.UniqueConstraint("pair_key", name="uq_friend_relations_pair_key"),
        db.CheckConstraint("user_id <> friend_user_id", name="ck_friend_not_self"),
        db.CheckConstraint("length(trim(relation_name)) > 0", name="ck_friend_relation_name_not_blank"),
        db.CheckConstraint(
            "friend_relation_name is null or length(trim(friend_relation_name)) > 0",
            name="ck_friend_reverse_relation_name_not_blank",
        ),
        db.CheckConstraint(
            "status in ('pending','active','revoked')",
            name="ck_friend_relations_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    friend_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    # Canonical "smaller-user-id:larger-user-id" key. It is nullable only for
    # rows created by older releases; every v12 write supplies it.
    pair_key = db.Column(db.String(50), nullable=True, index=True)
    relation_name = db.Column(db.String(80), nullable=False, default="亲友")
    friend_relation_name = db.Column(db.String(80), nullable=True)
    status = db.Column(
        db.String(20),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    auth_status = db.Column(db.Boolean, nullable=False, default=False)
    reverse_auth_status = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    authorization_version = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    booking_auth_status = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    reverse_booking_auth_status = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    booking_authorized_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reverse_booking_authorized_at = db.Column(db.DateTime(timezone=True), nullable=True)
    booking_authorization_version = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
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

    @staticmethod
    def canonical_pair_key(first_user_id, second_user_id):
        low, high = sorted((int(first_user_id), int(second_user_id)))
        return f"{low}:{high}"

    def counterparty_for(self, viewer_id):
        if int(viewer_id) == self.user_id:
            return self.friend_user
        if int(viewer_id) == self.friend_user_id:
            return self.user
        return None

    @property
    def is_active(self):
        return bool(
            self.status == "active"
            and self.auth_status
            and self.reverse_auth_status
            and self.booking_auth_status
            and self.reverse_booking_auth_status
        )

    @property
    def relationship_status(self):
        return self.status

    def activate(self, when=None):
        when = when or datetime.now(timezone.utc)
        changed = not self.is_active
        self.status = "active"
        self.auth_status = True
        self.reverse_auth_status = True
        self.booking_auth_status = True
        self.reverse_booking_auth_status = True
        self.booking_authorized_at = when
        self.reverse_booking_authorized_at = when
        self.accepted_at = when
        self.revoked_at = None
        if changed:
            self.authorization_version += 1
            self.booking_authorization_version += 1
        return changed

    def revoke(self, when=None):
        when = when or datetime.now(timezone.utc)
        if self.status != "revoked":
            self.authorization_version += 1
            self.booking_authorization_version += 1
        self.status = "revoked"
        self.auth_status = False
        self.reverse_auth_status = False
        self.booking_auth_status = False
        self.reverse_booking_auth_status = False
        self.booking_authorized_at = None
        self.reverse_booking_authorized_at = None
        self.revoked_at = when

    def reset_pending(self, *, requester_id, target_id, relation_name, when=None):
        when = when or datetime.now(timezone.utc)
        self.user_id = int(requester_id)
        self.friend_user_id = int(target_id)
        self.pair_key = self.canonical_pair_key(requester_id, target_id)
        self.relation_name = relation_name
        self.friend_relation_name = None
        self.status = "pending"
        self.auth_status = False
        self.reverse_auth_status = False
        self.booking_auth_status = False
        self.reverse_booking_auth_status = False
        self.booking_authorized_at = None
        self.reverse_booking_authorized_at = None
        self.accepted_at = None
        self.revoked_at = None
        self.created_at = when

    def health_granted(self, viewer_id, subject_id):
        """Whether subject has granted viewer access to subject health data."""
        viewer_id, subject_id = int(viewer_id), int(subject_id)
        return bool(
            self.is_active
            and {viewer_id, subject_id} == {self.user_id, self.friend_user_id}
        )

    def booking_granted(self, viewer_id, subject_id):
        return self.health_granted(viewer_id, subject_id)

    def booking_granted_at(self, viewer_id, subject_id):
        viewer_id, subject_id = int(viewer_id), int(subject_id)
        if self.health_granted(viewer_id, subject_id):
            return (
                self.booking_authorized_at
                if viewer_id == self.user_id
                else self.reverse_booking_authorized_at
            )
        return None

    def to_dict(self, *, viewer_id=None):
        viewer_id = int(viewer_id) if viewer_id is not None else None
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "friend_user_id": self.friend_user_id,
            "relation_name": self.relation_name,
            "status": self.relationship_status,
            "auth_status": self.is_active,
            "booking_auth_status": self.is_active,
            "booking_authorized_at": self.booking_authorized_at.isoformat() if self.booking_authorized_at else None,
            "created_at": self.created_at.isoformat(),
            "accepted_at": (
                self.accepted_at.isoformat() if self.accepted_at else None
            ),
            "revoked_at": (
                self.revoked_at.isoformat() if self.revoked_at else None
            ),
            "user": self._identity(self.user, authorized=self.is_active),
            "friend_user": self._identity(
                self.friend_user,
                authorized=self.is_active,
            ),
        }
        if viewer_id not in {self.user_id, self.friend_user_id}:
            return data
        counterparty = self.counterparty_for(viewer_id)
        viewer_is_creator = viewer_id == self.user_id
        relationship_active = self.is_active
        my_remark = (
            self.relation_name if viewer_is_creator else self.friend_relation_name
        ) or "亲友"
        their_remark = (
            self.friend_relation_name if viewer_is_creator else self.relation_name
        ) or "亲友"
        data.update(
            counterparty=self._identity(
                counterparty,
                authorized=relationship_active,
            ),
            my_remark=my_remark,
            their_remark=their_remark,
            relationship_status=self.relationship_status,
            request_initiator_user_id=self.user_id,
            can_accept=bool(
                self.status == "pending"
                and viewer_id == self.friend_user_id
            ),
            health_view_granted_to_me=relationship_active,
            health_view_granted_by_me=relationship_active,
            booking_granted_to_me=relationship_active,
            booking_granted_by_me=relationship_active,
            can_switch=relationship_active,
            authorization_version=self.authorization_version,
            booking_authorization_version=self.booking_authorization_version,
        )
        return data


class DelegationSessionAudit(db.Model):
    __tablename__ = "delegation_session_audits"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('active','exited','revoked','expired')",
            name="ck_delegation_session_status",
        ),
        db.CheckConstraint(
            "depth between 1 and 3",
            name="ck_delegation_session_depth",
        ),
    )

    id = db.Column(db.String(36), primary_key=True)
    actor_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_session_id = db.Column(
        db.String(36),
        db.ForeignKey("delegation_session_audits.id", ondelete="SET NULL"),
        nullable=True,
    )
    chain_user_ids = db.Column(db.JSON, nullable=False)
    relation_chain = db.Column(db.JSON, nullable=False)
    token_version_snapshot = db.Column(db.JSON, nullable=False)
    depth = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    ended_at = db.Column(db.DateTime(timezone=True), nullable=True)
    end_reason = db.Column(db.String(120), nullable=True)


class DelegatedActionAudit(db.Model):
    """Metadata-only audit trail; request and response bodies are never stored."""

    __tablename__ = "delegated_action_audits"
    __table_args__ = (
        db.CheckConstraint(
            "outcome in ('success','denied','error')",
            name="ck_delegated_action_outcome",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.String(36),
        db.ForeignKey("delegation_session_audits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_user_id = db.Column(db.Integer, nullable=False, index=True)
    subject_user_id = db.Column(db.Integer, nullable=False, index=True)
    chain_user_ids = db.Column(db.JSON, nullable=False)
    method = db.Column(db.String(10), nullable=False)
    path = db.Column(db.String(255), nullable=False)
    action = db.Column(db.String(120), nullable=False)
    outcome = db.Column(db.String(20), nullable=False)
    status_code = db.Column(db.Integer, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
