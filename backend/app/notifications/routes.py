from datetime import datetime, timezone

from flask import g, request

from app.extensions import db
from app.models import UserNotification
from app.notifications import notifications_bp
from app.services.permissions import ROLE_USER, roles_required


def _pagination(default_size=15):
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", default_size, type=int) or default_size, 1), 50)
    return page, size


@notifications_bp.get("")
@roles_required(ROLE_USER)
def list_notifications():
    page, size = _pagination()
    query = UserNotification.query.filter_by(user_id=g.current_user.id)
    unread = request.args.get("unread")
    if unread in {"1", "true"}:
        query = query.filter(UserNotification.read_at.is_(None))
    total = query.count()
    rows = query.order_by(
        UserNotification.created_at.desc(),
        UserNotification.id.desc(),
    ).offset((page - 1) * size).limit(size).all()
    unread_count = UserNotification.query.filter_by(
        user_id=g.current_user.id,
        read_at=None,
    ).count()
    return {
        "items": [row.to_dict() for row in rows],
        "unread_count": unread_count,
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200


@notifications_bp.get("/unread-count")
@roles_required(ROLE_USER)
def unread_count():
    count = UserNotification.query.filter_by(
        user_id=g.current_user.id,
        read_at=None,
    ).count()
    return {"unread_count": count}, 200


@notifications_bp.post("/<int:notification_id>/read")
@roles_required(ROLE_USER)
def mark_read(notification_id):
    item = UserNotification.query.filter_by(
        id=notification_id,
        user_id=g.current_user.id,
    ).first()
    if item is None:
        return {"message": "没有找到该站内通知"}, 404
    item.read_at = item.read_at or datetime.now(timezone.utc)
    db.session.commit()
    return {"item": item.to_dict()}, 200


@notifications_bp.post("/read-all")
@roles_required(ROLE_USER)
def mark_all_read():
    now = datetime.now(timezone.utc)
    updated = UserNotification.query.filter_by(
        user_id=g.current_user.id,
        read_at=None,
    ).update({"read_at": now}, synchronize_session=False)
    db.session.commit()
    return {"updated": updated}, 200
