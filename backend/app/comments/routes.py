from datetime import datetime, timedelta, timezone

from flask import g, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError

from app.comments import comments_bp
from app.extensions import db
from app.models import (
    Comment,
    CommentAppeal,
    CommentReply,
    CommentSanction,
    Institution,
    InstitutionReport,
    User,
)
from app.services.notifications import enqueue_user_notification
from app.services.permissions import ROLE_ADMIN, ROLE_INSTITUTION_ADMIN, ROLE_USER, role_error, roles_required
from app.services.user_access import profile_completion_error


def _current_user():
    user_id = int(get_jwt_identity())
    return db.session.get(User, user_id)


def _parse_optional_int(raw_value):
    if raw_value is None or raw_value == "":
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _parse_bool(raw_value):
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _normalize_content(raw_value):
    return (raw_value or "").strip()


def _require_admin(user: User):
    if user is None:
        return {"message": "账号不存在或已不可用"}, 404
    if user.role != "admin":
        return {"message": "只有系统管理员可以执行此操作"}, 403
    return None, None


def _is_admin(user: User | None) -> bool:
    return user is not None and user.role == "admin"


def _paginated(query, serializer, default_size=15):
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = min(max(request.args.get("page_size", default_size, type=int) or default_size, 1), 100)
    total = query.count()
    rows = query.offset((page - 1) * size).limit(size).all()
    return {
        "items": [serializer(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": size,
            "total": total,
            "pages": (total + size - 1) // size,
        },
    }, 200


def _pending_comment_expression():
    return db.and_(
        Comment.is_visible.is_(False),
        db.or_(
            Comment.hidden_reason.is_(None),
            db.func.length(db.func.trim(Comment.hidden_reason)) == 0,
        ),
    )


def _hidden_comment_expression():
    return db.and_(
        Comment.is_visible.is_(False),
        Comment.hidden_reason.is_not(None),
        db.func.length(db.func.trim(Comment.hidden_reason)) > 0,
    )


def _moderation_comment_counts(query):
    """Return queue counts before the selected queue/status is applied."""
    return {
        "comments_pending": query.filter(_pending_comment_expression()).count(),
        "replies_pending": query.filter(
            Comment.reply.has(CommentReply.status == "pending")
        ).count(),
        "all": query.count(),
    }


def _active_sanction(user_id):
    now = datetime.now(timezone.utc)
    CommentSanction.query.filter(
        CommentSanction.user_id == user_id,
        CommentSanction.status == "active",
        CommentSanction.expires_at.is_not(None),
        CommentSanction.expires_at <= now,
    ).update({"status": "expired"}, synchronize_session=False)
    return CommentSanction.query.filter(
        CommentSanction.user_id == user_id,
        CommentSanction.status == "active",
        db.or_(
            CommentSanction.expires_at.is_(None),
            CommentSanction.expires_at > now,
        ),
    ).order_by(CommentSanction.id.desc()).first()


def _claim_comment_sanction_slot(user_id):
    """Serialize the active-sanction check and insert for one user.

    SQLite ignores ``FOR UPDATE`` and openGauss cannot express a portable
    partial unique constraint through every supported deployment path.  A
    guarded no-op update on the owning user gives both databases a stable row
    to lock until the surrounding transaction commits.
    """
    changed = User.query.filter(
        User.id == user_id,
        User.role == ROLE_USER,
    ).update(
        {User.token_version: User.token_version},
        synchronize_session=False,
    )
    return changed == 1


def _review_reply_cas(reply_id, *, decision, admin_id, note, reviewed_at):
    values = {
        CommentReply.status: decision,
        CommentReply.reviewed_by_user_id: admin_id,
        CommentReply.reviewed_at: reviewed_at,
        CommentReply.review_note: note,
    }
    if decision == "approved":
        values[CommentReply.user_read_at] = None
    changed = CommentReply.query.filter(
        CommentReply.id == reply_id,
        CommentReply.status == "pending",
    ).update(values, synchronize_session=False)
    return changed == 1


def _review_appeal_cas(item, *, decision, note, admin_id, reviewed_at):
    changed = CommentAppeal.query.filter(
        CommentAppeal.id == item.id,
        CommentAppeal.status == "pending",
    ).update(
        {
            CommentAppeal.status: decision,
            CommentAppeal.review_note: note,
            CommentAppeal.reviewed_by_admin_id: admin_id,
            CommentAppeal.reviewed_at: reviewed_at,
        },
        synchronize_session=False,
    )
    return changed == 1


def _notify_moderated_user(user, *, event_type, key, title, body, payload):
    enqueue_user_notification(
        user,
        event_type=event_type,
        idempotency_key=key,
        title=title,
        body=body,
        action_url="/comments/mine",
        payload=payload,
    )


@comments_bp.get("")
@jwt_required()
def list_comments():
    user = _current_user()
    error = role_error(user, ROLE_USER)
    if error:
        return error

    institution_id = _parse_optional_int(request.args.get("institution_id"))
    include_hidden = _parse_bool(request.args.get("include_hidden")) or False

    query = Comment.query.order_by(Comment.created_at.desc(), Comment.id.desc())
    if institution_id is not None:
        query = query.filter_by(institution_id=institution_id)

    if user.role != "admin" or not include_hidden:
        query = query.filter_by(is_visible=True)

    # Ordinary-user catalog reads follow the same anonymity boundary as the
    # visitor catalog. Ownership and account names remain available only from
    # /mine, institution operations, and administrator moderation endpoints.
    from app.public_api.routes import public_comment_payload

    return _paginated(query, public_comment_payload)


@comments_bp.get("/mine")
@jwt_required()
def list_my_comments():
    user = _current_user()
    error = role_error(user, ROLE_USER)
    if error:
        return error

    institution_id = _parse_optional_int(request.args.get("institution_id"))
    query = Comment.query.filter_by(user_id=user.id).order_by(Comment.created_at.desc(), Comment.id.desc())
    if institution_id is not None:
        query = query.filter_by(institution_id=institution_id)

    return _paginated(query, lambda item: item.to_dict())


@comments_bp.get("/mine/sanction")
@roles_required(ROLE_USER)
def get_my_active_comment_sanction():
    sanction = _active_sanction(g.current_user.id)
    db.session.commit()
    return {
        "item": sanction.to_dict() if sanction else None,
        "has_active_sanction": sanction is not None,
        "appeal": (
            sanction.appeal.to_dict()
            if sanction and sanction.appeal
            else None
        ),
    }, 200


@comments_bp.get("/mine/unread-replies")
@roles_required(ROLE_USER)
def unread_reply_count():
    count = db.session.query(CommentReply.id).join(Comment).filter(
        Comment.user_id == g.current_user.id,
        CommentReply.status == "approved",
        CommentReply.user_read_at.is_(None),
    ).count()
    return {"count": count}, 200


@comments_bp.post("/mine/replies/read")
@roles_required(ROLE_USER)
def mark_replies_read():
    now = datetime.now(timezone.utc)
    rows = db.session.query(CommentReply).join(Comment).filter(
        Comment.user_id == g.current_user.id,
        CommentReply.status == "approved",
        CommentReply.user_read_at.is_(None),
    ).all()
    for row in rows:
        row.user_read_at = now
    db.session.commit()
    return {"message": "机构回复已标记为已读", "updated": len(rows)}, 200


@comments_bp.get("/organization")
@roles_required(ROLE_INSTITUTION_ADMIN)
def organization_comments():
    query = Comment.query.filter_by(
        institution_id=g.current_user.managed_institution_id,
        is_visible=True,
    ).order_by(Comment.created_at.desc(), Comment.id.desc())
    return _paginated(query, lambda row: row.to_dict(include_unapproved_reply=True))


@comments_bp.post("/<int:comment_id>/reply")
@roles_required(ROLE_INSTITUTION_ADMIN)
def submit_organization_reply(comment_id):
    comment = Comment.query.filter_by(
        id=comment_id,
        institution_id=g.current_user.managed_institution_id,
        is_visible=True,
    ).first()
    if comment is None:
        return {"message": "未找到可回复的公开评价"}, 404
    content = str((request.get_json(silent=True) or {}).get("content") or "").strip()
    if not content:
        return {"message": "请填写回复内容"}, 400
    if len(content) > 1000:
        return {"message": "回复内容不能超过1000个字符"}, 400
    reply = comment.reply
    if reply and reply.status in {"pending", "approved"}:
        message = "该回复正在等待管理员审核" if reply.status == "pending" else "该评价已经有审核通过的机构回复"
        return {"message": message}, 409
    if reply is None:
        reply = CommentReply(comment_id=comment.id, institution_id=comment.institution_id)
        db.session.add(reply)
    reply.content = content
    reply.status = "pending"
    reply.submitted_by_user_id = g.current_user.id
    reply.submitted_at = datetime.now(timezone.utc)
    reply.reviewed_by_user_id = None
    reply.reviewed_at = None
    reply.review_note = None
    reply.user_read_at = None
    db.session.commit()
    return {"item": reply.to_dict(), "message": "机构回复已提交，等待管理员审核"}, 201


@comments_bp.post("")
@jwt_required()
def create_comment():
    user = _current_user()
    error = role_error(user, ROLE_USER)
    if error:
        return error
    identity_error = profile_completion_error(user)
    if identity_error:
        return identity_error
    sanction = _active_sanction(user.id)
    if sanction is not None:
        db.session.commit()
        return {
            "message": "您的评价发布权限当前已被限制，可在申诉页面提交一次申诉",
            "code": "COMMENT_BANNED",
            "sanction": sanction.to_dict(),
        }, 403

    payload = request.get_json(silent=True) or {}
    institution_id = _parse_optional_int(payload.get("institution_id"))
    content = (payload.get("content") or "").strip()
    rating = _parse_optional_int(payload.get("rating"))

    if institution_id is None:
        return {"message": "请选择要评价的体检机构"}, 400

    institution = db.session.get(Institution, institution_id)
    if institution is None or not institution.is_active:
        return {"message": "没有找到可评价的体检机构"}, 404

    if not content:
        return {"message": "请填写评价内容"}, 400

    if len(content) > 1000:
        return {"message": "评价内容不能超过1000个字符"}, 400

    if rating is None or rating < 1 or rating > 5:
        return {"message": "请选择1至5星评分"}, 400

    uploaded_record = InstitutionReport.query.filter_by(
        matched_user_id=user.id,
        institution_id=institution_id,
        status="published",
    ).first()
    if uploaded_record is None:
        return {
            "code": "comment_requires_record",
            "message": "在该机构完成体检并收到正式归档结果后才能评价",
        }, 403

    comment = Comment(
        user_id=user.id,
        institution_id=institution_id,
        content=content,
        rating=rating,
        is_visible=False,
    )
    db.session.add(comment)
    db.session.commit()

    return {"item": comment.to_dict(), "message": "评价已提交，等待管理员审核"}, 201


@comments_bp.get("/moderation")
@jwt_required()
def list_comments_for_moderation():
    user = _current_user()
    error_payload, error_status = _require_admin(user)
    if error_payload:
        return error_payload, error_status

    institution_id = _parse_optional_int(request.args.get("institution_id"))
    query = Comment.query
    if institution_id is not None:
        query = query.filter_by(institution_id=institution_id)

    counts = _moderation_comment_counts(query)
    queue = str(
        request.args.get("queue")
        or request.args.get("moderation_type")
        or "all"
    ).strip().lower()
    if queue not in {"comments", "replies", "all"}:
        return {"message": "审核队列只支持 comments、replies 或 all"}, 400

    comment_status = str(request.args.get("comment_status") or "").strip().lower()
    reply_status = str(request.args.get("reply_status") or "").strip().lower()
    if queue == "comments" and not comment_status:
        comment_status = "pending"
    if queue == "replies" and not reply_status:
        reply_status = "pending"

    if comment_status and comment_status not in {"all", "pending", "visible", "hidden"}:
        return {"message": "评论审核状态不正确"}, 400
    if reply_status and reply_status not in {
        "all",
        "none",
        "pending",
        "approved",
        "rejected",
    }:
        return {"message": "机构回复审核状态不正确"}, 400

    if comment_status == "pending":
        query = query.filter(_pending_comment_expression())
    elif comment_status == "visible":
        query = query.filter(Comment.is_visible.is_(True))
    elif comment_status == "hidden":
        query = query.filter(_hidden_comment_expression())

    if reply_status == "none":
        query = query.filter(~Comment.reply.has())
    elif reply_status in {"pending", "approved", "rejected"}:
        query = query.filter(Comment.reply.has(CommentReply.status == reply_status))

    response, status_code = _paginated(
        query.order_by(Comment.created_at.desc(), Comment.id.desc()),
        lambda item: item.to_dict(include_unapproved_reply=True),
    )
    response["counts"] = counts
    response["filters"] = {
        "queue": queue,
        "comment_status": comment_status or "all",
        "reply_status": reply_status or "all",
    }
    return response, status_code


@comments_bp.post("/replies/<int:reply_id>/approve")
@roles_required(ROLE_ADMIN)
def approve_reply(reply_id):
    reply = db.session.get(CommentReply, reply_id)
    if reply is None:
        return {"message": "未找到机构回复"}, 404
    reviewed_at = datetime.now(timezone.utc)
    if not _review_reply_cas(
        reply.id,
        decision="approved",
        admin_id=g.current_user.id,
        note=None,
        reviewed_at=reviewed_at,
    ):
        db.session.rollback()
        return {"message": "只有待审核的机构回复可以通过"}, 409
    db.session.commit()
    reply = db.session.get(CommentReply, reply_id)
    return {"item": reply.to_dict(), "message": "机构回复已审核通过"}, 200


@comments_bp.post("/replies/<int:reply_id>/reject")
@roles_required(ROLE_ADMIN)
def reject_reply(reply_id):
    reply = db.session.get(CommentReply, reply_id)
    if reply is None:
        return {"message": "未找到机构回复"}, 404
    note = str((request.get_json(silent=True) or {}).get("review_note") or "").strip()
    review_note = note or "回复内容未通过审核，请修改后重新提交"
    if not _review_reply_cas(
        reply.id,
        decision="rejected",
        admin_id=g.current_user.id,
        note=review_note,
        reviewed_at=datetime.now(timezone.utc),
    ):
        db.session.rollback()
        return {"message": "只有待审核的机构回复可以驳回"}, 409
    db.session.commit()
    reply = db.session.get(CommentReply, reply_id)
    return {"item": reply.to_dict(), "message": "机构回复已驳回"}, 200


@comments_bp.put("/<int:comment_id>/visibility")
@jwt_required()
def update_comment_visibility(comment_id: int):
    user = _current_user()
    error_payload, error_status = _require_admin(user)
    if error_payload:
        return error_payload, error_status

    comment = db.session.get(Comment, comment_id)
    if comment is None:
        return {"message": "comment not found"}, 404

    payload = request.get_json(silent=True) or {}
    is_visible = _parse_bool(payload.get("is_visible"))
    if is_visible is None:
        return {"message": "is_visible must be boolean"}, 400

    reason = str(payload.get("reason") or payload.get("hidden_reason") or "").strip()
    if not is_visible and not reason:
        return {"message": "隐藏评价时必须填写审核原因"}, 400
    if len(reason) > 500:
        return {"message": "审核原因不能超过500个字符"}, 400
    comment.is_visible = is_visible
    if not is_visible:
        comment.hidden_reason = reason
    comment.moderated_by_user_id = user.id
    comment.moderated_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"item": comment.to_dict()}, 200


@comments_bp.put("/<int:comment_id>")
@jwt_required()
def update_comment(comment_id: int):
    user = _current_user()
    error_payload, error_status = _require_admin(user)
    if error_payload:
        return error_payload, error_status

    del comment_id
    return {
        "message": "评价原文和评分不可由管理员修改，请使用审核可见性与禁言接口",
        "code": "COMMENT_ORIGINAL_IMMUTABLE",
    }, 410


@comments_bp.get("/moderation/sanctions")
@roles_required(ROLE_ADMIN)
def list_comment_sanctions():
    now = datetime.now(timezone.utc)
    CommentSanction.query.filter(
        CommentSanction.status == "active",
        CommentSanction.expires_at.is_not(None),
        CommentSanction.expires_at <= now,
    ).update({"status": "expired"}, synchronize_session=False)
    query = CommentSanction.query
    status = str(request.args.get("status") or "").strip()
    if status:
        query = query.filter_by(status=status)
    user_id = request.args.get("user_id", type=int)
    if user_id:
        query = query.filter_by(user_id=user_id)
    db.session.commit()
    return _paginated(
        query.order_by(CommentSanction.created_at.desc(), CommentSanction.id.desc()),
        lambda row: row.to_dict(),
    )


@comments_bp.post("/moderation/sanctions")
@roles_required(ROLE_ADMIN)
def create_comment_sanction():
    payload = request.get_json(silent=True) or {}
    source_comment = None
    if payload.get("source_comment_id") not in {None, ""}:
        try:
            source_comment_id = int(payload.get("source_comment_id"))
        except (TypeError, ValueError):
            return {"message": "source_comment_id must be an integer"}, 400
        source_comment = db.session.get(Comment, source_comment_id)
        if source_comment is None:
            return {"message": "未找到来源评价"}, 404
    try:
        user_id = int(payload.get("user_id") or (
            source_comment.user_id if source_comment else None
        ))
    except (TypeError, ValueError):
        return {"message": "user_id is required"}, 400
    user = db.session.get(User, user_id)
    if user is None or user.role != ROLE_USER:
        return {"message": "未找到可限制评价权限的用户"}, 404
    if source_comment is not None and source_comment.user_id != user.id:
        return {
            "message": "来源评价与被禁言用户不一致",
            "code": "COMMENT_SANCTION_SUBJECT_MISMATCH",
        }, 409
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        return {"message": "请填写禁言原因"}, 400
    if len(reason) > 500:
        return {"message": "禁言原因不能超过500个字符"}, 400
    raw_duration = payload.get("duration_days")
    if raw_duration in {None, "", "permanent"}:
        duration_days = None
    else:
        try:
            duration_days = int(raw_duration)
        except (TypeError, ValueError):
            return {"message": "禁言期限只支持7天、30天或永久"}, 400
        if duration_days not in {7, 30}:
            return {"message": "禁言期限只支持7天、30天或永久"}, 400
    if not _claim_comment_sanction_slot(user.id):
        return {"message": "未找到可限制评价权限的用户"}, 404
    active = _active_sanction(user.id)
    if active is not None:
        return {
            "message": "该用户已有生效中的评价禁言",
            "code": "COMMENT_SANCTION_ALREADY_ACTIVE",
            "item": active.to_dict(),
        }, 409
    now = datetime.now(timezone.utc)
    item = CommentSanction(
        user_id=user.id,
        source_comment_id=source_comment.id if source_comment else None,
        reason=reason,
        duration_days=duration_days,
        status="active",
        starts_at=now,
        expires_at=now + timedelta(days=duration_days) if duration_days else None,
        created_by_admin_id=g.current_user.id,
    )
    db.session.add(item)
    if source_comment is not None:
        source_comment.is_visible = False
        source_comment.hidden_reason = reason
        source_comment.moderated_by_user_id = g.current_user.id
        source_comment.moderated_at = now
    db.session.flush()
    duration_label = "永久" if duration_days is None else f"{duration_days}天"
    _notify_moderated_user(
        user,
        event_type="comment_sanction_created",
        key=f"comment-sanction:{item.id}:created",
        title="您的评价发布权限已被限制",
        body=f"原因：{reason}；期限：{duration_label}。您可以提交一次申诉。",
        payload={"sanction_id": item.id, "duration_days": duration_days},
    )
    db.session.commit()
    return {"item": item.to_dict(), "message": "评价禁言已生效"}, 201


def _lift_sanction(item, *, reason):
    now = datetime.now(timezone.utc)
    changed = CommentSanction.query.filter(
        CommentSanction.id == item.id,
        CommentSanction.status == "active",
    ).update(
        {
            CommentSanction.status: "lifted",
            CommentSanction.lifted_by_admin_id: g.current_user.id,
            CommentSanction.lifted_at: now,
            CommentSanction.lift_reason: reason,
        },
        synchronize_session=False,
    )
    if changed != 1:
        return False
    db.session.expire(item)
    _notify_moderated_user(
        item.user,
        event_type="comment_sanction_lifted",
        key=f"comment-sanction:{item.id}:lifted",
        title="您的评价发布权限已恢复",
        body=f"平台已解除评价禁言。说明：{reason}",
        payload={"sanction_id": item.id},
    )
    return True


@comments_bp.post("/moderation/sanctions/<int:sanction_id>/lift")
@roles_required(ROLE_ADMIN)
def lift_comment_sanction(sanction_id):
    item = db.session.get(CommentSanction, sanction_id)
    if item is None:
        return {"message": "未找到该禁言记录"}, 404
    reason = str((request.get_json(silent=True) or {}).get("reason") or "").strip()
    if not reason:
        return {"message": "请填写解封说明"}, 400
    if len(reason) > 500:
        return {"message": "解封说明不能超过500个字符"}, 400
    if not _lift_sanction(item, reason=reason):
        db.session.rollback()
        return {"message": "只有生效中的禁言可以解除"}, 409
    db.session.commit()
    item = db.session.get(CommentSanction, sanction_id)
    return {"item": item.to_dict(), "message": "用户评价权限已恢复"}, 200


@comments_bp.get("/appeals")
@roles_required(ROLE_USER, ROLE_ADMIN)
def list_comment_appeals():
    query = CommentAppeal.query
    if g.current_user.role == ROLE_USER:
        query = query.filter_by(user_id=g.current_user.id)
    counts = {
        "pending": query.filter_by(status="pending").count(),
        "approved": query.filter_by(status="approved").count(),
        "rejected": query.filter_by(status="rejected").count(),
        "all": query.count(),
    }
    status = str(request.args.get("status") or "all").strip().lower()
    if status not in {"all", "pending", "approved", "rejected"}:
        return {"message": "申诉状态不正确"}, 400
    if status != "all":
        query = query.filter_by(status=status)
    response, status_code = _paginated(
        query.order_by(CommentAppeal.submitted_at.desc(), CommentAppeal.id.desc()),
        lambda row: {
            **row.to_dict(),
            "sanction": row.sanction.to_dict() if row.sanction else None,
        },
    )
    response["counts"] = counts
    response["filters"] = {"status": status}
    return response, status_code


@comments_bp.post("/appeals")
@roles_required(ROLE_USER)
def create_comment_appeal():
    identity_error = profile_completion_error(g.current_user)
    if identity_error:
        return identity_error
    payload = request.get_json(silent=True) or {}
    try:
        sanction_id = int(payload.get("sanction_id"))
    except (TypeError, ValueError):
        return {"message": "sanction_id is required"}, 400
    sanction = CommentSanction.query.filter_by(
        id=sanction_id,
        user_id=g.current_user.id,
    ).first()
    if sanction is None:
        return {"message": "未找到该禁言记录"}, 404
    active = _active_sanction(g.current_user.id)
    if active is None or active.id != sanction.id:
        db.session.commit()
        return {"message": "该禁言已不再生效，无需申诉"}, 409
    if sanction.appeal is not None:
        return {
            "message": "每条禁言记录只能提交一次申诉",
            "code": "COMMENT_APPEAL_ALREADY_SUBMITTED",
            "item": sanction.appeal.to_dict(),
        }, 409
    content = str(payload.get("content") or "").strip()
    if not content:
        return {"message": "请填写申诉说明"}, 400
    if len(content) > 2000:
        return {"message": "申诉说明不能超过2000个字符"}, 400
    item = CommentAppeal(
        sanction_id=sanction.id,
        user_id=g.current_user.id,
        content=content,
        status="pending",
    )
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if CommentAppeal.query.filter_by(sanction_id=sanction.id).first():
            return {
                "message": "每条禁言记录只能提交一次申诉",
                "code": "COMMENT_APPEAL_ALREADY_SUBMITTED",
            }, 409
        return {"message": "申诉提交冲突，请刷新后重试"}, 409
    return {"item": item.to_dict(), "message": "申诉已提交，等待平台审核"}, 201


def _review_appeal(appeal_id, decision):
    item = db.session.get(CommentAppeal, appeal_id)
    if item is None:
        return {"message": "未找到该申诉记录"}, 404
    payload = request.get_json(silent=True) or {}
    note = str(payload.get("review_note") or payload.get("reason") or "").strip()
    if not note:
        return {"message": "请填写申诉审核说明"}, 400
    if len(note) > 500:
        return {"message": "申诉审核说明不能超过500个字符"}, 400
    now = datetime.now(timezone.utc)
    if not _review_appeal_cas(
        item,
        decision=decision,
        note=note,
        admin_id=g.current_user.id,
        reviewed_at=now,
    ):
        db.session.rollback()
        return {"message": "只有待审核申诉可以处理"}, 409
    db.session.expire(item)
    if decision == "approved":
        if not _lift_sanction(item.sanction, reason=f"申诉通过：{note}"):
            _notify_moderated_user(
                item.user,
                event_type="comment_appeal_approved",
                key=f"comment-appeal:{item.id}:approved",
                title="您的评价禁言申诉已通过",
                body=f"平台审核说明：{note}",
                payload={
                    "appeal_id": item.id,
                    "sanction_id": item.sanction_id,
                },
            )
    else:
        _notify_moderated_user(
            item.user,
            event_type="comment_appeal_rejected",
            key=f"comment-appeal:{item.id}:rejected",
            title="您的评价禁言申诉未通过",
            body=f"平台审核说明：{note}",
            payload={"appeal_id": item.id, "sanction_id": item.sanction_id},
        )
    db.session.commit()
    message = "申诉已通过，用户评价权限已恢复" if decision == "approved" else "申诉已驳回，禁言继续生效"
    return {"item": item.to_dict(), "message": message}, 200


@comments_bp.post("/appeals/<int:appeal_id>/approve")
@roles_required(ROLE_ADMIN)
def approve_comment_appeal(appeal_id):
    return _review_appeal(appeal_id, "approved")


@comments_bp.post("/appeals/<int:appeal_id>/reject")
@roles_required(ROLE_ADMIN)
def reject_comment_appeal(appeal_id):
    return _review_appeal(appeal_id, "rejected")


@comments_bp.post("/appeals/<int:appeal_id>/review")
@roles_required(ROLE_ADMIN)
def review_comment_appeal(appeal_id):
    decision = str((request.get_json(silent=True) or {}).get("decision") or "").strip()
    if decision not in {"approved", "rejected"}:
        return {"message": "decision must be approved or rejected"}, 400
    return _review_appeal(appeal_id, decision)


@comments_bp.delete("/<int:comment_id>")
@jwt_required()
def delete_comment(comment_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    comment = db.session.get(Comment, comment_id)
    if comment is None:
        return {"message": "comment not found"}, 404

    if _is_admin(user):
        return {
            "message": "管理员不能硬删除评价，请使用隐藏与治理接口保留审计原文",
            "code": "COMMENT_HARD_DELETE_FORBIDDEN",
        }, 409
    if user.role != ROLE_USER or comment.user_id != user.id:
        return {"message": "无权删除该评价"}, 403
    identity_error = profile_completion_error(user)
    if identity_error:
        return identity_error

    comment.is_visible = False
    comment.hidden_reason = "用户已删除展示"
    comment.moderated_by_user_id = None
    comment.moderated_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"message": "评价已从公开页面移除，历史原文已保留"}, 200
