from __future__ import annotations

import re
from collections.abc import Mapping


HEALTH_ID_REDACTION = "[健康身份码已脱敏]"
_HEALTH_ID_PATTERN = re.compile(
    r"HID-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}",
    re.IGNORECASE,
)


def redact_health_identity_codes(value):
    """Return a copy with complete health identity codes removed recursively.

    The issued-code substring is sensitive even when pasted next to ASCII or
    CJK text. DLP therefore removes every ``HID-`` plus eight-character match
    from the unambiguous alphabet without relying on word boundaries. A small
    amount of over-redaction is preferable to allowing a real code to escape.
    """

    if isinstance(value, str):
        return _HEALTH_ID_PATTERN.sub(HEALTH_ID_REDACTION, value)
    if isinstance(value, Mapping):
        redacted = {}
        for key, item in value.items():
            safe_key = (
                redact_health_identity_codes(key) if isinstance(key, str) else key
            )
            redacted[safe_key] = redact_health_identity_codes(item)
        return redacted
    if isinstance(value, list):
        return [redact_health_identity_codes(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_health_identity_codes(item) for item in value)
    if isinstance(value, set):
        return {redact_health_identity_codes(item) for item in value}
    return value
