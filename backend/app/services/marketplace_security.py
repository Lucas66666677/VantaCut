"""Envelope encryption helpers for private marketplace template instructions."""
from __future__ import annotations

import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class MarketplaceSecurityError(RuntimeError):
    pass


def _fernet() -> Fernet:
    if not settings.marketplace_template_encryption_key:
        raise MarketplaceSecurityError("MARKETPLACE_TEMPLATE_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(settings.marketplace_template_encryption_key.encode("ascii"))
    except Exception as exc:
        raise MarketplaceSecurityError("MARKETPLACE_TEMPLATE_ENCRYPTION_KEY is not a valid Fernet key") from exc


def canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encrypt_template_payload(payload: dict) -> tuple[str, str]:
    plaintext = canonical_json(payload)
    return _fernet().encrypt(plaintext).decode("ascii"), hashlib.sha256(plaintext).hexdigest()


def decrypt_template_payload(ciphertext: str, expected_sha256: str) -> dict:
    try:
        plaintext = _fernet().decrypt(ciphertext.encode("ascii"))
    except InvalidToken as exc:
        raise MarketplaceSecurityError("Marketplace template cannot be decrypted") from exc
    digest = hashlib.sha256(plaintext).hexdigest()
    if digest != expected_sha256:
        raise MarketplaceSecurityError("Marketplace template integrity check failed")
    decoded = json.loads(plaintext)
    if not isinstance(decoded, dict):
        raise MarketplaceSecurityError("Marketplace template payload is invalid")
    return decoded
