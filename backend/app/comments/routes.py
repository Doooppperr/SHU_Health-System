from datetime import datetime, timezone

from flask import g, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.comments import comments_bp
from app.extensions import db
from app.models import Comment, CommentReply, Institution, InstitutionReport, User
from app.services.permissions import ROLE_ADMIN, ROLE_INSTITUTION_ADMIN, ROLE_USER, role_error, roles_required


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

    items = query.all()
    return {"items": [item.to_dict() for item in items]}, 200


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

    items = query.all()
    return {"items": [item.to_dict() for item in items]}, 200


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
    rows = Comment.query.filter_by(
        institution_id=g.current_user.managed_institution_id,
        is_visible=True,
    ).order_by(Comment.created_at.desc(), Comment.id.desc()).all()
    return {"items": [row.to_dict(include_unapproved_reply=True) for row in rows]}, 200


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
    query = Comment.query.order_by(Comment.created_at.desc(), Comment.id.desc())
    if institution_id is not None:
        query = query.filter_by(institution_id=institution_id)

    items = query.all()
    return {"items": [item.to_dict(include_unapproved_reply=True) for item in items]}, 200


@comments_bp.post("/replies/<int:reply_id>/approve")
@roles_required(ROLE_ADMIN)
def approve_reply(reply_id):
    reply = db.session.get(CommentReply, reply_id)
    if reply is None:
        return {"message": "未找到机构回复"}, 404
    if reply.status != "pending":
        return {"message": "只有待审核的机构回复可以通过"}, 409
    reply.status = "approved"
    reply.reviewed_by_user_id = g.current_user.id
    reply.reviewed_at = datetime.now(timezone.utc)
    reply.review_note = None
    reply.user_read_at = None
    db.session.commit()
    return {"item": reply.to_dict(), "message": "机构回复已审核通过"}, 200


@comments_bp.post("/replies/<int:reply_id>/reject")
@roles_required(ROLE_ADMIN)
def reject_reply(reply_id):
    reply = db.session.get(CommentReply, reply_id)
    if reply is None:
        return {"message": "未找到机构回复"}, 404
    if reply.status != "pending":
        return {"message": "只有待审核的机构回复可以驳回"}, 409
    note = str((request.get_json(silent=True) or {}).get("review_note") or "").strip()
    reply.status = "rejected"
    reply.reviewed_by_user_id = g.current_user.id
    reply.reviewed_at = datetime.now(timezone.utc)
    reply.review_note = note or "回复内容未通过审核，请修改后重新提交"
    db.session.commit()
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

    comment.is_visible = is_visible
    db.session.commit()
    return {"item": comment.to_dict()}, 200


@comments_bp.put("/<int:comment_id>")
@jwt_required()
def update_comment(comment_id: int):
    user = _current_user()
    error_payload, error_status = _require_admin(user)
    if error_payload:
        return error_payload, error_status

    comment = db.session.get(Comment, comment_id)
    if comment is None:
        return {"message": "comment not found"}, 404

    payload = request.get_json(silent=True) or {}

    if "content" in payload:
        content = _normalize_content(payload.get("content"))
        if not content:
            return {"message": "content is required"}, 400
        if len(content) > 1000:
            return {"message": "content is too long"}, 400
        comment.content = content

    if "rating" in payload:
        rating = _parse_optional_int(payload.get("rating"))
        if rating is None or rating < 1 or rating > 5:
            return {"message": "rating must be between 1 and 5"}, 400
        comment.rating = rating

    if "is_visible" in payload:
        is_visible = _parse_bool(payload.get("is_visible"))
        if is_visible is None:
            return {"message": "is_visible must be boolean"}, 400
        comment.is_visible = is_visible

    db.session.commit()
    return {"item": comment.to_dict()}, 200


@comments_bp.delete("/<int:comment_id>")
@jwt_required()
def delete_comment(comment_id: int):
    user = _current_user()
    if user is None:
        return {"message": "user not found"}, 404

    comment = db.session.get(Comment, comment_id)
    if comment is None:
        return {"message": "comment not found"}, 404

    if not _is_admin(user) and (user.role != ROLE_USER or comment.user_id != user.id):
        return {"message": "无权删除该评价"}, 403

    db.session.delete(comment)
    db.session.commit()
    return {"message": "评价已删除"}, 200
