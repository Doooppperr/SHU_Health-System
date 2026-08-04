from __future__ import annotations

import re

from flask import current_app, g, request
from flask_jwt_extended import get_jwt, verify_jwt_in_request

from app.extensions import db
from app.models import DelegatedActionAudit
_DELEGATED_ACCOUNT_DEACTIVATION_PATHS = (
    re.compile(r"^/api/org/account/deactivate$"),
    re.compile(r"^/api/users/\d+$"),
)
_SENSITIVE_READ_PREFIXES = (
    "/api/self-measurements",
    "/api/health-data",
    "/api/health-trends",
    "/api/health/",
    "/api/exam-reports",
    "/api/ai/records",
    "/api/profile/me",
    "/api/users/me",
    "/api/booking-intake-defaults",
    "/api/appointments",
    "/api/booking-groups",
    "/api/complaints",
    "/api/friends",
    "/api/notifications",
)


def capture_delegated_request():
    """Validate a presented JWT early and capture its audit identity."""
    if not request.path.startswith("/api/"):
        return None
    authorization = request.headers.get("Authorization") or ""
    if not authorization.lower().startswith("bearer "):
        return None

    # Accept either access or refresh tokens here; the endpoint's own
    # @jwt_required still enforces the expected token type.
    verify_jwt_in_request(optional=True, verify_type=False)
    claims = get_jwt()
    if claims.get("delegated") is not True:
        return None

    g.delegated_audit_context = {
        "session_id": claims.get("delegation_session_id"),
        "actor_user_id": int(claims.get("actor_id")),
        "subject_user_id": int(claims.get("subject_id")),
        "chain_user_ids": [int(value) for value in claims.get("chain") or []],
    }
    if (
        request.method in {"POST", "DELETE"}
        and any(
            pattern.fullmatch(request.path)
            for pattern in _DELEGATED_ACCOUNT_DEACTIVATION_PATHS
        )
    ):
        return {
            "message": "关联账号登录状态下不能注销账号，请先退出并由账号本人操作",
            "code": "DELEGATION_ACCOUNT_DEACTIVATION_FORBIDDEN",
        }, 403
    return None


def audit_delegated_response(response):
    context = getattr(g, "delegated_audit_context", None)
    if not context:
        return response
    should_record = request.method not in {"GET", "HEAD", "OPTIONS"} or request.path.startswith(
        _SENSITIVE_READ_PREFIXES
    )
    if not should_record:
        return response
    if response.status_code < 400:
        outcome = "success"
    elif response.status_code in {401, 403}:
        outcome = "denied"
    else:
        outcome = "error"
    try:
        db.session.add(
            DelegatedActionAudit(
                session_id=context["session_id"],
                actor_user_id=context["actor_user_id"],
                subject_user_id=context["subject_user_id"],
                chain_user_ids=context["chain_user_ids"],
                method=request.method,
                path=request.path[:255],
                action=(request.endpoint or "unmatched")[:120],
                outcome=outcome,
                status_code=response.status_code,
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Unable to persist delegated action audit")
    return response
