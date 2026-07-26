from __future__ import annotations

from app.services.contact import normalize_email


def effective_account_email(user) -> str | None:
    """Return the single authoritative destination for an account."""
    if (
        user is not None
        and user.role == "institution_admin"
        and user.managed_institution is not None
        and user.managed_institution.notification_email
    ):
        return normalize_email(user.managed_institution.notification_email)
    return normalize_email(user.email if user is not None else None)


def synchronize_institution_email(institution, email: str, *, verified_at=None) -> None:
    """Keep compatibility user rows aligned with a branch's canonical email."""
    normalized = normalize_email(email)
    institution.notification_email = normalized
    for administrator in institution.administrators:
        administrator.email = normalized
        administrator.email_verified_at = verified_at
