import asyncio
import json
import os
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from redis import asyncio as redis_async
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.progress import REDIS_URL, project_status_channel, project_status_key
from app.core.security import TokenError, decode_access_token
from app.db.session import get_db
from app.models.entities import Project, User

router = APIRouter(prefix="/projects", tags=["project-status"])


def _project_not_found() -> HTTPException:
    # Same response whether the project truly doesn't exist or exists but
    # belongs to someone else — do not confirm the existence of another
    # user's private project to an unauthorized caller.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")


def _authorize_project(db: Session, project_id: UUID, current_user: User) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise _project_not_found()
    return project


async def project_status_events(project_id: str, request: Request) -> AsyncGenerator[str, None]:
    client = redis_async.from_url(os.getenv("REDIS_URL", REDIS_URL), decode_responses=True)
    pubsub = client.pubsub()
    try:
        cached = await client.get(project_status_key(project_id))
        if cached:
            yield f"event: status\ndata: {cached}\n\n"
        else:
            initial = json.dumps({"project_id": project_id, "progress": 0, "status": "idle", "stage": "idle"})
            yield f"event: status\ndata: {initial}\n\n"

        await pubsub.subscribe(project_status_channel(project_id))
        while not await request.is_disconnected():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                yield f"event: status\ndata: {message['data']}\n\n"
            else:
                # SSE comment keeps proxies and idle browser connections alive.
                yield ": keepalive\n\n"
            await asyncio.sleep(0)
    finally:
        await pubsub.unsubscribe(project_status_channel(project_id))
        await pubsub.aclose()
        await client.aclose()


@router.get("/{project_id}/status")
async def stream_project_status(
    project_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    # Ownership is verified BEFORE the StreamingResponse is constructed, so an
    # unauthorized caller gets a plain 404 JSON response and the Redis
    # subscription (project_status_events) is never entered.
    _authorize_project(db, project_id, current_user)
    return StreamingResponse(
        project_status_events(str(project_id), request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _authenticate_websocket(websocket: WebSocket, db: Session) -> User | None:
    """Extract and verify a bearer token from the WebSocket handshake, returning
    the authenticated user or None.

    Browsers cannot set an `Authorization` header (or any custom header) on the
    `WebSocket` constructor, so the normal `get_current_user` HTTP dependency
    doesn't apply here. Cookie-based auth would require changing how
    POST /auth/login issues tokens (app/api/v1/auth.py, PR #1's scaffolding) —
    out of scope for this batch, see the checkpoint doc.

    Instead this uses the `Sec-WebSocket-Protocol` field, which a browser CAN
    set programmatically (`new WebSocket(url, ["bearer", token])`) without any
    change to how tokens are issued or stored. This is preferred over a `?token=`
    query parameter: query strings land in browser history, `Referer` headers to
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


@router.websocket("/{project_id}/status/ws")
async def stream_project_status_websocket(
    websocket: WebSocket,
    project_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    """WebSocket twin of the SSE status stream for GPU jobs and rich editor clients.

    Authentication and ownership are both verified before `websocket.accept()` —
    no live project data is ever sent to an unauthenticated caller or to an
    authenticated caller who does not own this project. Connections that fail
    either check are closed with code 1008 (Policy Violation) without ever
    being accepted, which the ASGI/WebSocket handshake treats as a rejected
    connection rather than an accepted-then-closed one.
    """
    user = await _authenticate_websocket(websocket, db)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    project = db.get(Project, project_id)
    if project is None or project.owner_id != user.id:
        # Same 1008 code and no message for "no such project" vs "not yours" —
        # do not give an unauthorized caller a signal either way.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept(subprotocol="bearer")
    client = redis_async.from_url(os.getenv("REDIS_URL", REDIS_URL), decode_responses=True)
    pubsub = client.pubsub()
    try:
        cached = await client.get(project_status_key(str(project_id)))
        await websocket.send_text(cached or json.dumps({
            "project_id": str(project_id), "progress": 0, "status": "idle", "stage": "idle",
        }))
        await pubsub.subscribe(project_status_channel(str(project_id)))
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                await websocket.send_text(message["data"])
            else:
                await websocket.send_json({"kind": "keepalive"})
            await asyncio.sleep(0)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(project_status_channel(str(project_id)))
        await pubsub.aclose()
        await client.aclose()
