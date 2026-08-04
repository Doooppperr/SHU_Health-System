from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app


_PREFIX = "v1:"


def _key() -> bytes:
    configured = str(
        current_app.config.get("ACCOUNT_CREDENTIAL_ENCRYPTION_KEY") or ""
    ).strip()
    if not configured:
        raise RuntimeError("ACCOUNT_CREDENTIAL_ENCRYPTION_KEY is not configured")
    return hashlib.sha256(configured.encode("utf-8")).digest()


def encrypt_account_credentials(value: dict, *, purpose: str) -> str:
    plaintext = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key()).encrypt(
        nonce,
        plaintext,
        purpose.encode("utf-8"),
    )
    return _PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_account_credentials(value: str, *, purpose: str) -> dict:
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        raise ValueError("unsupported encrypted account credentials")
    raw = base64.urlsafe_b64decode(value[len(_PREFIX):].encode("ascii"))
    plaintext = AESGCM(_key()).decrypt(
        raw[:12],
        raw[12:],
        purpose.encode("utf-8"),
    )
    decoded = json.loads(plaintext.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("invalid encrypted account credentials")
    return decoded
