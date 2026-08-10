import asyncio
import json
import os
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from redis import asyncio as redis_async

from app.core.progress import REDIS_URL, project_status_channel, project_status_key

router = APIRouter(prefix="/projects", tags=["project-status"])


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
async def stream_project_status(project_id: UUID, request: Request) -> StreamingResponse:
    return StreamingResponse(
        project_status_events(str(project_id), request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/{project_id}/status/ws")
async def stream_project_status_websocket(websocket: WebSocket, project_id: UUID) -> None:
    """WebSocket twin of the SSE status stream for GPU jobs and rich editor clients.

    Authentication should be applied before `accept` when the production auth dependency lands.
    """
    await websocket.accept()
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
