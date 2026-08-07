from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from flask import current_app, request


_buckets = defaultdict(deque)
_lock = threading.Lock()


def ai_rate_limited(
    user=None,
    *,
    scope: str = "chat",
    guest_config_key: str = "AI_GUEST_RATE_LIMIT_PER_MINUTE",
    auth_config_key: str = "AI_AUTH_RATE_LIMIT_PER_MINUTE",
    guest_default: int = 10,
    auth_default: int = 30,
) -> bool:
    """Apply the process-local sliding-window guard used by AI entry points."""

    limit_key = auth_config_key if user else guest_config_key
    limit = int(current_app.config.get(limit_key, auth_default if user else guest_default))
    identity = f"user:{user.id}" if user else f"guest:{request.remote_addr or 'unknown'}"
    bucket_key = f"{scope}:{identity}"
    now = time.monotonic()
    with _lock:
        bucket = _buckets[bucket_key]
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= max(limit, 0):
            return True
        bucket.append(now)
    return False
