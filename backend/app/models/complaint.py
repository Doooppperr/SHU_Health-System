from datetime import datetime, timezone

from app.extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class AppointmentComplaint(db.Model):
    __tablename__ = "appointment_complaints"
    __table_args__ = (
        db.UniqueConstraint("appointment_id", name="uq_appointment_complaints_appointment"),
        db.CheckConstraint(
            "status in ('institution_pending','user_confirmation','platform_pending','platform_processing','resolved')",
            name="ck_appointment_complaints_status",
        ),
        db.CheckConstraint("length(trim(content)) > 0", name="ck_appointment_complaints_content"),
    )

    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(
        db.Integer,
        db.ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    institution_id = db.Column(
        db.Integer,
        db.ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    complainant_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    complainant_username_snapshot = db.Column(db.String(80), nullable=False)
    category = db.Column(db.String(50), nullable=False, default="service")
    content = db.Column(db.Text, nullable=False)
    status = db.Column(
        db.String(40),
        nullable=False,
        default="institution_pending",
        server_default="institution_pending",
        index=True,
    )
    institution_reply = db.Column(db.Text, nullable=True)
    institution_replied_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    institution_replied_at = db.Column(db.DateTime(timezone=True), nullable=True)
    escalation_reason = db.Column(db.Text, nullable=True)
    escalated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    admin_reply = db.Column(db.Text, nullable=True)
    handled_by_admin_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    handled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    appointment = db.relationship("Appointment")
    institution = db.relationship("Institution")
    complainant = db.relationship("User", foreign_keys=[complainant_user_id])
    institution_replier = db.relationship("User", foreign_keys=[institution_replied_by_user_id])
    admin_handler = db.relationship("User", foreign_keys=[handled_by_admin_id])
    events = db.relationship(
        "ComplaintEvent",
        back_populates="complaint",
        cascade="all, delete-orphan",
        order_by="ComplaintEvent.created_at.asc()",
    )
    messages = db.relationship(
        "ComplaintMessage",
        back_populates="complaint",
        cascade="all, delete-orphan",
        order_by=lambda: (
            ComplaintMessage.created_at.asc(),
            ComplaintMessage.id.asc(),
        ),
    )
    refund_case = db.relationship(
        "RefundCase",
        back_populates="complaint",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def to_dict(self, *, include_events=True):
        appointment = self.appointment
        result = {
            "id": self.id,
            "appointment_id": self.appointment_id,
            "institution_id": self.institution_id,
            "category": self.category,
            "content": self.content,
            "status": self.status,
            "status_label": {
                "institution_pending": "待机构处理",
                "user_confirmation": "待用户确认",
                "platform_pending": "待平台处理",
                "platform_processing": "平台处理中",
                "resolved": "已解决",
            }.get(self.status, "处理中"),
            "complainant": {
                "id": self.complainant_user_id,
                "username": self.complainant_username_snapshot,
            },
            "institution": {
                "id": self.institution.id,
                "name": self.institution.organization.name if self.institution.organization else self.institution.name,
                "branch_name": self.institution.branch_name,
            } if self.institution else None,
            "appointment": {
                "id": appointment.id,
                "appointment_date": appointment.appointment_date.isoformat(),
                "package_name": appointment.package_name_snapshot,
                "subject_name": appointment.user_name_snapshot,
                "status": appointment.status,
            } if appointment else None,
            "institution_reply": self.institution_reply,
            "institution_replied_at": self.institution_replied_at.isoformat() if self.institution_replied_at else None,
            "escalation_reason": self.escalation_reason,
            "escalated_at": self.escalated_at.isoformat() if self.escalated_at else None,
            "admin_reply": self.admin_reply,
            "handled_at": self.handled_at.isoformat() if self.handled_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_events:
            result["events"] = [event.to_dict() for event in self.events]
        result["messages"] = [message.to_dict() for message in self.messages]
        result["refund"] = self.refund_case.to_dict() if self.refund_case else None
        return result


class ComplaintEvent(db.Model):
    __tablename__ = "complaint_events"
    __table_args__ = (
        db.CheckConstraint(
            "event_type in ('created','institution_replied','user_confirmed','escalated','admin_started','admin_replied','admin_resolved')",
            name="ck_complaint_events_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(
        db.Integer,
        db.ForeignKey("appointment_complaints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = db.Column(db.String(40), nullable=False, index=True)
    actor_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_role = db.Column(db.String(30), nullable=False)
    content = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    complaint = db.relationship("AppointmentComplaint", back_populates="events")
    actor = db.relationship("User")

    def to_dict(self):
        return {
            "id": self.id,
            "event_type": self.event_type,
            "actor_role": self.actor_role,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ComplaintMessage(db.Model):
    __tablename__ = "complaint_messages"
    __table_args__ = (
        db.CheckConstraint(
            "sender_role in ('user','institution_admin','admin')",
            name="ck_complaint_messages_sender_role",
        ),
        db.CheckConstraint(
            "length(trim(content)) > 0",
            name="ck_complaint_messages_content",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(
        db.Integer,
        db.ForeignKey("appointment_complaints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sender_role = db.Column(db.String(30), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    complaint = db.relationship("AppointmentComplaint", back_populates="messages")
    sender = db.relationship("User")

    def to_dict(self):
        return {
            "id": self.id,
            "sender_user_id": self.sender_user_id,
            "sender_role": self.sender_role,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
