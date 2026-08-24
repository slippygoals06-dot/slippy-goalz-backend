"""Encrypt sensitive secrets at rest (WhatsApp / social access tokens).

Uses Fernet with a key derived from SECRET_KEY. Legacy plaintext values still decrypt
as pass-through so existing rows keep working until re-saved.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import SECRET_KEY

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: Optional[str]) -> Optional[str]:
    if plain is None:
        return None
    s = str(plain).strip()
    if not s:
        return ""
    if s.startswith(_PREFIX):
        return s
    token = _fernet().encrypt(s.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_secret(stored: Optional[str]) -> Optional[str]:
    if stored is None:
        return None
    s = str(stored)
    if not s:
        return ""
    if not s.startswith(_PREFIX):
        # Legacy plaintext row
        return s
    blob = s[len(_PREFIX) :]
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
