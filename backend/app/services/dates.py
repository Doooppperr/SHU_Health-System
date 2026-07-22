from __future__ import annotations

from datetime import date, datetime


def calendar_date(value) -> date | None:
    """Normalize SQLite/openGauss DATE values to a calendar date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def calendar_date_iso(value) -> str | None:
    normalized = calendar_date(value)
    return normalized.isoformat() if normalized else None
