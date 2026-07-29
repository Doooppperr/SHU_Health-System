from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app


_PREFIX = "v1:"


def _key() -> bytes:
    configured = str(current_app.config.get("AGENT_DATA_ENCRYPTION_KEY") or "").strip()
    if not configured:
        configured = str(current_app.config.get("JWT_SECRET_KEY") or "")
    try:
        decoded = base64.urlsafe_b64decode(configured + "=" * (-len(configured) % 4))
    except (ValueError, TypeError):
        decoded = b""
    if len(decoded) == 32:
        return decoded
    return hashlib.sha256(configured.encode("utf-8")).digest()


def encrypt_json(value, *, purpose: str) -> str:
    plaintext = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, plaintext, purpose.encode("utf-8"))
    return _PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_json(value: str, *, purpose: str):
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        raise ValueError("unsupported encrypted value")
    raw = base64.urlsafe_b64decode(value[len(_PREFIX):].encode("ascii"))
    plaintext = AESGCM(_key()).decrypt(raw[:12], raw[12:], purpose.encode("utf-8"))
    return json.loads(plaintext.decode("utf-8"))
