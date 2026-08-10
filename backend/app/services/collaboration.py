"""Redis-backed binary Yjs update relay plus ephemeral collaboration presence."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as redis
from fastapi import WebSocket

from app.core.config import settings


YJS_PREFIX = b"Y"
PRESENCE_PREFIX = b"P"
UPDATE_TTL_SECONDS = 24 * 60 * 60


@dataclass
class TimelineRoom:
    clients: set[WebSocket] = field(default_factory=set)
    listener_task: asyncio.Task[None] | None = None


class CollaborationHub:
    """Relay rooms across API replicas through Redis Pub/Sub and a bounded update log."""

    def __init__(self) -> None:
        self._redis: redis.Redis[bytes] | None = None
        self._rooms: dict[str, TimelineRoom] = {}

    async def _client(self) -> redis.Redis[bytes]:
        if self._redis is None:
            self._redis = redis.from_url(settings.redis_url, decode_responses=False)
        return self._redis

    @staticmethod
    def _channel(timeline_id: str) -> str:
        return f"collaboration:timeline:{timeline_id}:events"

    @staticmethod
    def _update_key(timeline_id: str) -> str:
        return f"collaboration:timeline:{timeline_id}:updates"

    async def join(self, timeline_id: str, websocket: WebSocket) -> None:
        client = await self._client()
        # A reconnecting client applies idempotent Yjs updates before receiving live events.
        for update in await client.lrange(self._update_key(timeline_id), 0, -1):
            await websocket.send_bytes(update)
        room = self._rooms.setdefault(timeline_id, TimelineRoom())
        room.clients.add(websocket)
        if room.listener_task is None or room.listener_task.done():
            room.listener_task = asyncio.create_task(self._listen(timeline_id, room))

    async def leave(self, timeline_id: str, websocket: WebSocket) -> None:
        room = self._rooms.get(timeline_id)
        if room is None:
            return
        room.clients.discard(websocket)
        if not room.clients:
            if room.listener_task:
                room.listener_task.cancel()
            self._rooms.pop(timeline_id, None)

    async def publish_update(self, timeline_id: str, update: bytes) -> None:
        if len(update) > 1_000_000:
            raise ValueError("Yjs update exceeds 1 MB limit")
        client = await self._client()
        pipeline = client.pipeline()
        pipeline.rpush(self._update_key(timeline_id), update)
        pipeline.expire(self._update_key(timeline_id), UPDATE_TTL_SECONDS)
        pipeline.publish(self._channel(timeline_id), YJS_PREFIX + update)
        await pipeline.execute()

    async def publish_presence(self, timeline_id: str, payload: str) -> None:
        if len(payload.encode("utf-8")) > 16_000:
            raise ValueError("Presence payload exceeds 16 KB limit")
        client = await self._client()
        await client.publish(self._channel(timeline_id), PRESENCE_PREFIX + payload.encode("utf-8"))

    async def _listen(self, timeline_id: str, room: TimelineRoom) -> None:
        client = await self._client()
        pubsub = client.pubsub()
        await pubsub.subscribe(self._channel(timeline_id))
        try:
            while room.clients:
                message: dict[str, Any] | None = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue
                payload = message.get("data")
                if not isinstance(payload, bytes) or not payload:
                    continue
                dead_clients: list[WebSocket] = []
                for websocket in room.clients.copy():
                    try:
                        if payload.startswith(YJS_PREFIX):
                            await websocket.send_bytes(payload[1:])
                        elif payload.startswith(PRESENCE_PREFIX):
                            await websocket.send_text(payload[1:].decode("utf-8"))
                    except Exception:
                        dead_clients.append(websocket)
                for websocket in dead_clients:
                    room.clients.discard(websocket)
        finally:
            await pubsub.unsubscribe(self._channel(timeline_id))
            await pubsub.aclose()


collaboration_hub = CollaborationHub()
