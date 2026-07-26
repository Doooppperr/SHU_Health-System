from app.extensions import db
from app.models import NotificationOutbox, UserNotification
from app.services.account_email import effective_account_email


def enqueue_user_notification(
    user,
    *,
    event_type,
    idempotency_key,
    title,
    body,
    action_url=None,
    payload=None,
    email_payload=None,
):
    existing = UserNotification.query.filter_by(
        user_id=user.id,
        idempotency_key=idempotency_key,
    ).first()
    if existing is None:
        existing = UserNotification(
            user_id=user.id,
            event_type=event_type,
            idempotency_key=idempotency_key,
            title=title,
            body=body,
            action_url=action_url,
            payload=payload or {},
        )
        db.session.add(existing)

    recipient = effective_account_email(user)
    if recipient and email_payload is not None:
        outbox_key = f"user:{user.id}:{idempotency_key}:email"
        if NotificationOutbox.query.filter_by(idempotency_key=outbox_key).first() is None:
            db.session.add(
                NotificationOutbox(
                    event_type=event_type,
                    idempotency_key=outbox_key,
                    recipient=recipient,
                    payload=email_payload,
                )
            )
    return existing
