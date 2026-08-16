"""Password hashing and JWT access-token utilities.

Kept deliberately small and dependency-light: bcrypt for hashing (no custom
crypto), PyJWT for signing/verification with a single symmetric secret. This
module should never be imported for anything other than credential handling
so it stays easy to audit in isolation.

Never log, print, or return `plain_password`, `hashed_password`, or
`settings.jwt_secret_key` from any function here.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

ALGORITHM = "HS256"
TOKEN_TYPE_ACCESS = "access"


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password. Raises ValueError for obviously-too-weak input;
    real strength policy belongs in the request schema (see app.auth.schemas)."""
    if len(plain_password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time-ish verification via bcrypt.checkpw. Any malformed stored
    hash (e.g. a legacy/empty value) is treated as a verification failure
    rather than raising, so callers can always branch on a plain bool."""
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


class TokenError(Exception):
    """Raised for any invalid, expired, or malformed token. Callers should
    collapse every TokenError into the same generic 401 response — do not
    surface *why* a token failed, to avoid giving an attacker a signal."""


def create_access_token(user_id: uuid.UUID, expires_minutes: int | None = None) -> str:
    if not settings.jwt_secret_key:
        raise RuntimeError("JWT_SECRET_KEY is not configured; refusing to mint a token.")
    now = datetime.now(UTC)
    minutes = expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": TOKEN_TYPE_ACCESS,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID:
    if not settings.jwt_secret_key:
        raise TokenError("JWT secret is not configured")
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid or expired token") from exc
    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise TokenError("Wrong token type")
    subject = payload.get("sub")
    if not subject:
        raise TokenError("Token missing subject")
    try:
        return uuid.UUID(str(subject))
    except ValueError as exc:
        raise TokenError("Token subject is not a valid user id") from exc
