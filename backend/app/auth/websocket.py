"""Shared WebSocket bearer-token authentication.

Extracted from app/api/v1/project_status.py (Batch 1) during Batch 2A so
app/api/v1/collaboration.py can reuse the identical authentication logic
without importing anything under the `app.api.v1` package. That constraint
is not stylistic: a normal `from app.api.v1.project_status import ...`
statement forces Python to import the `app.api` and `app.api.v1` packages
first (to resolve the dotted path), which runs `app/api/__init__.py` —
which eagerly imports all ~75 v1 routers, several of which import heavy,
not-installed-in-this-CI-slice dependencies (confirmed by CI evidence:
`ModuleNotFoundError: No module named 'torch'`, via
app.api.v1.analysis -> ... -> app.ml.retention_model). Living under
`app.auth` instead (whose own `__init__.py` is empty) avoids that entirely,
matching the same reasoning tests/conftest.py's `_load_auth_router` and the
Batch 1 test files already document for *test* code — this module applies
the identical constraint to *production* code that needs to be importable
from a narrow test slice.

project_status.py re-exports this as `_authenticate_websocket` (see that
module) so its own code and tests are unchanged.
"""
from __future__ import annotations

from fastapi import WebSocket
from sqlalchemy.orm import Session

from app.core.security import TokenError, decode_access_token
from app.models.entities import User


async def authenticate_websocket_bearer(websocket: WebSocket, db: Session) -> User | None:
    """Extract and verify a bearer token from a WebSocket handshake, returning
    the authenticated user or None.

    Browsers cannot set an `Authorization` header (or any custom header) on
    the `WebSocket` constructor, so the normal `get_current_user` HTTP
    dependency doesn't apply here. Instead this uses the
    `Sec-WebSocket-Protocol` field, which a browser CAN set programmatically
    (`new WebSocket(url, ["bearer", token])`) without any change to how
    tokens are issued or stored. This is preferred over a `?token=` query
    parameter: query strings land in browser history, `Referer` headers to
    third-party resources, and are far more commonly captured verbatim by
    default proxy/webserver access-log configs than a WebSocket subprotocol
    header is. The token is never logged by this function.

    Convention: the client offers exactly two subprotocol values, `"bearer"`
    and the access token, e.g. `Sec-WebSocket-Protocol: bearer, <token>`. If
    the server accepts the connection it must echo back `subprotocol="bearer"`
    in `websocket.accept()` per the WebSocket handshake spec (a server must
    select one of the offered subprotocols to accept from among them).
    """
    raw = websocket.headers.get("sec-websocket-protocol", "")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None
    token = parts[1]
    try:
        user_id = decode_access_token(token)
    except TokenError:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user
