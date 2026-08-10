"""Short-lived mobile capabilities and in-process WebRTC signalling relay.

The media path intentionally never traverses the signalling websocket.  Phones
send WebRTC directly to the editor browser for preview, while independently
playable MediaRecorder chunks use the authenticated ingest endpoint.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from fastapi import WebSocket

from app.core.config import settings


class WirelessCameraTokenError(ValueError):
    pass


@dataclass(frozen=True)
class WirelessCameraCapability:
    pairing_id: UUID
    project_id: UUID
    timeline_id: UUID
    session_id: UUID
    expires_at: int


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signature(payload: str) -> str:
    return _b64(hmac.new(settings.mobile_handoff_token_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())


def issue_wireless_camera_token(*, pairing_id: UUID, project_id: UUID, timeline_id: UUID, session_id: UUID) -> tuple[str, datetime]:
    expires_at = int(time.time()) + settings.mobile_handoff_ttl_seconds
    body = _b64(json.dumps({
        "pairing_id": str(pairing_id), "project_id": str(project_id), "timeline_id": str(timeline_id),
        "session_id": str(session_id), "exp": expires_at, "nonce": secrets.token_urlsafe(12),
    }, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_signature(body)}", datetime.fromtimestamp(expires_at, tz=UTC)


def verify_wireless_camera_token(token: str, pairing_id: UUID | None = None) -> WirelessCameraCapability:
    try:
        body, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _signature(body)):
            raise WirelessCameraTokenError("Invalid pairing signature")
        payload = json.loads(_unb64(body))
        capability = WirelessCameraCapability(
            pairing_id=UUID(payload["pairing_id"]), project_id=UUID(payload["project_id"]),
            timeline_id=UUID(payload["timeline_id"]), session_id=UUID(payload["session_id"]), expires_at=int(payload["exp"]),
        )
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise WirelessCameraTokenError("Invalid wireless camera token") from exc
    if capability.expires_at < int(time.time()):
        raise WirelessCameraTokenError("Wireless camera pairing has expired")
    if pairing_id and capability.pairing_id != pairing_id:
        raise WirelessCameraTokenError("Pairing token does not match this camera")
    return capability


@dataclass
class SignallingRoom:
    editor: WebSocket | None = None
    mobile: WebSocket | None = None


@dataclass
class WirelessSignallingRegistry:
    rooms: dict[UUID, SignallingRoom] = field(default_factory=dict)

    async def connect(self, pairing_id: UUID, role: str, socket: WebSocket) -> None:
        room = self.rooms.setdefault(pairing_id, SignallingRoom())
        if role == "editor":
            room.editor = socket
        else:
            room.mobile = socket
        await self._presence(pairing_id)

    async def relay(self, pairing_id: UUID, role: str, payload: dict[str, object]) -> None:
        room = self.rooms.get(pairing_id)
        if room is None:
            return
        peer = room.mobile if role == "editor" else room.editor
        if peer is not None:
            await peer.send_json(payload)

    async def disconnect(self, pairing_id: UUID, role: str, socket: WebSocket) -> None:
        room = self.rooms.get(pairing_id)
        if room is None:
            return
        if role == "editor" and room.editor is socket:
            room.editor = None
        if role == "mobile" and room.mobile is socket:
            room.mobile = None
        await self._presence(pairing_id)
        if room.editor is None and room.mobile is None:
            self.rooms.pop(pairing_id, None)

    async def _presence(self, pairing_id: UUID) -> None:
        room = self.rooms.get(pairing_id)
        if room is None:
            return
        payload = {"type": "presence", "editor_connected": room.editor is not None, "mobile_connected": room.mobile is not None}
        for peer in (room.editor, room.mobile):
            if peer is not None:
                await peer.send_json(payload)


wireless_signalling = WirelessSignallingRegistry()
