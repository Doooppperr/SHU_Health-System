from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import update

from app.extensions import db
from app.models.account_security import PasswordVerificationChallenge
from app.models.user import User


def increment_user_security_epochs(
    user_ids,
    *,
    token_version: bool = True,
    booking_authorization_version: bool = False,
) -> dict[int, dict[str, int]]:
    """Atomically increment account epochs in stable user-id order.

    Python read/modify/write (``user.token_version += 1``) can lose one of two
    concurrent security changes. Database expressions serialize increments on
    each row. Updating multiple users in sorted order also avoids cross-request
    lock-order inversions on organization-wide state changes.
    """

    if isinstance(user_ids, (int, User)):
        user_ids = [user_ids]
    normalized_ids = sorted({
        int(item.id if isinstance(item, User) else item)
        for item in user_ids
    })
    if not normalized_ids:
        return {}
    if not token_version and not booking_authorization_version:
        raise ValueError("at least one security epoch must be incremented")

    # Flush password/role/active/email mutations first; the epoch UPDATE and
    # those fields still commit or roll back together in the caller's
    # transaction.
    db.session.flush()
    result = {}
    for user_id in normalized_ids:
        values = {}
        if token_version:
            values["token_version"] = User.token_version + 1
        if booking_authorization_version:
            values["booking_authorization_version"] = (
                User.booking_authorization_version + 1
            )
        updated = db.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(**values),
            execution_options={"synchronize_session": False},
        )
        if updated.rowcount != 1:
            raise LookupError(f"user security epoch target not found: {user_id}")
        user = db.session.get(User, user_id, populate_existing=True)
        result[user_id] = {
            "token_version": int(user.token_version),
            "booking_authorization_version": int(
                user.booking_authorization_version
            ),
        }
    return result


def consume_password_challenges(user_id: int, *, consumed_at=None) -> int:
    """Revoke every outstanding password/email verification challenge.

    The caller owns the surrounding transaction so the challenge revocation
    commits atomically with the password, email, role or active-state change.
    """

    consumed_at = consumed_at or datetime.now(timezone.utc)
    return PasswordVerificationChallenge.query.filter_by(
        user_id=int(user_id),
        consumed_at=None,
    ).update(
        {"consumed_at": consumed_at},
        synchronize_session=False,
    )


def revoke_account_security_artifacts(user_id: int, *, revoked_at=None) -> dict:
    """Retire recovery challenges and OAuth grants in the caller's transaction."""

    # Import lazily because demo/bootstrap modules use this service while the
    # model package is still being registered.
    from app.models.v11 import (
        OAuthAccessToken,
        OAuthAuthorizationCode,
        OAuthRefreshToken,
    )

    revoked_at = revoked_at or datetime.now(timezone.utc)
    return {
        "password_challenges": consume_password_challenges(
            user_id,
            consumed_at=revoked_at,
        ),
        "oauth_authorization_codes": OAuthAuthorizationCode.query.filter_by(
            user_id=int(user_id),
            consumed_at=None,
        ).update(
            {"consumed_at": revoked_at},
            synchronize_session=False,
        ),
        "oauth_access_tokens": OAuthAccessToken.query.filter_by(
            user_id=int(user_id),
            revoked_at=None,
        ).update(
            {"revoked_at": revoked_at},
            synchronize_session=False,
        ),
        "oauth_refresh_tokens": OAuthRefreshToken.query.filter_by(
            user_id=int(user_id),
            revoked_at=None,
        ).update(
            {"revoked_at": revoked_at},
            synchronize_session=False,
        ),
    }


def claim_password_challenge(
    challenge_id: int,
    *,
    user_id: int,
    token_version: int,
    claimed_at=None,
) -> bool:
    """Atomically consume one still-valid challenge before account mutation.

    A conditional UPDATE is deliberately used instead of mutating an ORM row.
    Concurrent confirmations race on the database row and exactly one can
    observe ``rowcount == 1``.
    """

    claimed_at = claimed_at or datetime.now(timezone.utc)
    rowcount = PasswordVerificationChallenge.query.filter(
        PasswordVerificationChallenge.id == int(challenge_id),
        PasswordVerificationChallenge.user_id == int(user_id),
        PasswordVerificationChallenge.consumed_at.is_(None),
        PasswordVerificationChallenge.expires_at > claimed_at,
        PasswordVerificationChallenge.attempt_count.between(1, 5),
        PasswordVerificationChallenge.token_version_snapshot
        == int(token_version),
        PasswordVerificationChallenge.token_version_snapshot
        == db.select(User.token_version)
        .where(User.id == PasswordVerificationChallenge.user_id)
        .scalar_subquery(),
    ).update(
        {"consumed_at": claimed_at},
        synchronize_session=False,
    )
    return rowcount == 1


def reserve_password_challenge_attempt(
    challenge_id: int,
    *,
    user_id: int,
    token_version: int,
    attempted_at=None,
) -> bool:
    """Atomically reserve one of the challenge's five attempt slots."""

    attempted_at = attempted_at or datetime.now(timezone.utc)
    rowcount = PasswordVerificationChallenge.query.filter(
        PasswordVerificationChallenge.id == int(challenge_id),
        PasswordVerificationChallenge.user_id == int(user_id),
        PasswordVerificationChallenge.consumed_at.is_(None),
        PasswordVerificationChallenge.expires_at > attempted_at,
        PasswordVerificationChallenge.attempt_count < 5,
        PasswordVerificationChallenge.token_version_snapshot
        == int(token_version),
        PasswordVerificationChallenge.token_version_snapshot
        == db.select(User.token_version)
        .where(User.id == PasswordVerificationChallenge.user_id)
        .scalar_subquery(),
    ).update(
        {
            "attempt_count": (
                PasswordVerificationChallenge.attempt_count + 1
            ),
        },
        synchronize_session=False,
    )
    return rowcount == 1
