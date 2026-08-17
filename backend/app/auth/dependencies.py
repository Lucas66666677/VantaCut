"""Verified-identity dependency for FastAPI routes.

Every route that touches user-owned data should depend on `get_current_user`
instead of trusting a client-supplied `user_id`/`owner_id` field in the
request body or query string. As of this commit, most of the ~75 v1 route
files still do the latter (see
artifacts/service-readiness/vantacut-auth-route-map.md for the full
inventory and the file-by-file migration plan) — this dependency is the
building block that migration will use, applied one route group at a time
in a follow-up, reviewable PR.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import TokenError, decode_access_token
from app.db.session import get_db
from app.models.entities import User

# tokenUrl is documentation-only (drives the OpenAPI "Authorize" button); the
# actual login route lives at POST /api/v1/auth/login (app/api/v1/auth.py).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise _credentials_error()
    try:
        user_id = decode_access_token(token)
    except TokenError as exc:
        raise _credentials_error() from exc
    user = db.get(User, user_id)
    if user is None:
        raise _credentials_error()
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled")
    return user
