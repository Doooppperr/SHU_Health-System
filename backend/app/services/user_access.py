from __future__ import annotations

from functools import wraps

from flask import g, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from app.extensions import db


PROFILE_COMPLETION_ERROR = {
    "message": "请先完成实名认证后再使用该功能",
    "code": "IDENTITY_REQUIRED",
}
IDENTITY_WRITE_ALLOWLIST = {
    "/api/profile/me/complete",
    "/api/auth/password-change/code",
    "/api/auth/password-change/confirm",
    "/api/auth/email",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/auth/delegation/back",
    "/api/auth/delegation/exit",
}


def profile_completion_error(user):
    if user is None or user.role != "user" or not user.profile_completed:
        return PROFILE_COMPLETION_ERROR.copy(), 409
    return None


def complete_profile_required(view):
    """Guard an ordinary-user business mutation with one stable response."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        error = profile_completion_error(getattr(g, "current_user", None))
        if error:
            return error
        return view(*args, **kwargs)

    return wrapped


def enforce_completed_identity_for_writes():
    """Apply the v12 identity gate to every authenticated user mutation.

    Keeping this at the application boundary prevents a new business endpoint
    from accidentally bypassing the rule merely because it forgot a decorator.
    """

    if (
        not request.path.startswith("/api/")
        or request.method in {"GET", "HEAD", "OPTIONS"}
        or request.path in IDENTITY_WRITE_ALLOWLIST
    ):
        return None
    authorization = request.headers.get("Authorization") or ""
    if not authorization.lower().startswith("bearer "):
        return None
    verify_jwt_in_request(optional=True, verify_type=False)
    identity = get_jwt_identity()
    from app.models import User

    try:
        user = db.session.get(User, int(identity))
    except (TypeError, ValueError):
        return None
    if user is not None and user.role == "user" and not user.profile_completed:
        return PROFILE_COMPLETION_ERROR.copy(), 409
    return None
