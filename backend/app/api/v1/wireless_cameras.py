"""QR-paired browser cameras: direct WebRTC preview plus aligned chunk ingest."""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import CameraDevice, CameraIngestChunk, CameraIngestSession, Project, Timeline, User
from app.schemas.wireless_camera import (
    CreateWirelessCameraPairingRequest, WirelessCameraChunkResponse, WirelessCameraCompleteResponse,
    WirelessCameraClockResponse, WirelessCameraPairingResponse, WirelessCameraPairingStatus, WirelessCameraStartRequest, WirelessCameraStartResponse,
)
from app.services.mobile_handoff import qr_code_data_uri
from app.services.storage import upload_object
from app.services.wireless_camera import (
    WirelessCameraTokenError, issue_wireless_camera_token, verify_wireless_camera_token, wireless_signalling,
)
from app.tasks.ingest_tasks import process_camera_ingest_chunk

router = APIRouter(prefix="/timelines/{timeline_id}/wireless-cameras", tags=["wireless-cameras"])
mobile_router = APIRouter(prefix="/wireless-cameras", tags=["wireless-cameras"])


def _owned_timeline(timeline_id: UUID, user_id: UUID, db: Session) -> Timeline:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    project, user = db.get(Project, timeline.project_id), db.get(User, user_id)
    if project is None or user is None or project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot operate this timeline")
    return timeline


def _token_or_401(token: str | None, pairing_id: UUID):
    if not token:
        raise HTTPException(status_code=401, detail="Missing wireless camera capability")
    try:
        return verify_wireless_camera_token(token, pairing_id)
    except WirelessCameraTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/pairings", response_model=WirelessCameraPairingResponse, status_code=status.HTTP_201_CREATED)
def create_pairing(
    timeline_id: UUID,
    payload: CreateWirelessCameraPairingRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WirelessCameraPairingResponse:
    timeline = _owned_timeline(timeline_id, current_user.id, db)
    settings = dict(timeline.settings_json or {})
    multicam = dict(settings.get("wireless_multicam") or {})
    cameras = list(multicam.get("cameras") or [])
    active = [item for item in cameras if item.get("status") in {"paired", "capturing"}]
    if len(active) >= 2:
        raise HTTPException(status_code=409, detail="This MVP supports at most two active wireless cameras")

    now_ms = int(time.time() * 1000)
    origin_ms = int(multicam.get("capture_origin_ms") or (now_ms + 3_000))
    pairing_id, capture_id = uuid4(), f"wireless-{uuid4().hex[:16]}"
    index = len(cameras) + 1
    device = CameraDevice(
        project_id=timeline.project_id, device_identifier=f"browser-{pairing_id}", display_name=payload.label,
        # This flow authenticates with a scoped capability token rather than the
        # hardware HMAC endpoint, so it never exposes a reusable device secret.
        device_type="wireless_browser", encrypted_hmac_secret="capability-token-only",
        metadata_json={"wireless_pairing_id": str(pairing_id), "camera_index": index, "auth_mode": "capability_token"},
    )
    db.add(device); db.flush()
    session = CameraIngestSession(
        project_id=timeline.project_id, device_id=device.id, timeline_id=timeline.id, capture_id=capture_id,
        metadata_json={"wireless_multicam": {"pairing_id": str(pairing_id), "camera_index": index, "label": payload.label,
                                             "capture_origin_ms": origin_ms, "track": "multicam_video", "timeline_offset_seconds": 0.0}},
    )
    db.add(session); db.flush()
    token, expires_at = issue_wireless_camera_token(pairing_id=pairing_id, project_id=timeline.project_id, timeline_id=timeline.id, session_id=session.id)
    mobile_url = f"{settings.web_app_base_url.rstrip('/')}/wireless-camera?pairing={pairing_id}&token={token}"
    cameras.append({"pairing_id": str(pairing_id), "session_id": str(session.id), "label": payload.label, "camera_index": index, "status": "paired"})
    timeline.settings_json = {**settings, "wireless_multicam": {**multicam, "capture_origin_ms": origin_ms, "cameras": cameras}}
    db.commit()
    return WirelessCameraPairingResponse(pairing_id=pairing_id, session_id=session.id, timeline_id=timeline.id, label=payload.label,
        camera_index=index, mobile_url=mobile_url, qr_code_data_uri=qr_code_data_uri(mobile_url), expires_at=expires_at,
        server_epoch_ms=now_ms, capture_origin_ms=origin_ms)


@router.get("/pairings", response_model=list[WirelessCameraPairingStatus])
def list_pairings(
    timeline_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WirelessCameraPairingStatus]:
    # Previously took `user_id` as a plain query parameter, letting anyone
    # who knew a target user_id + timeline_id enumerate that user's
    # wireless-camera pairings. Caller identity now comes exclusively from
    # the verified bearer token.
    timeline = _owned_timeline(timeline_id, current_user.id, db)
    cameras = (timeline.settings_json or {}).get("wireless_multicam", {}).get("cameras", [])
    return [WirelessCameraPairingStatus.model_validate(item) for item in cameras]


@mobile_router.post("/pairings/{pairing_id}/start", response_model=WirelessCameraStartResponse)
def start_recording(pairing_id: UUID, payload: WirelessCameraStartRequest, x_wireless_camera_token: str | None = Header(default=None), db: Session = Depends(get_db)) -> WirelessCameraStartResponse:
    capability = _token_or_401(x_wireless_camera_token, pairing_id)
    session = db.get(CameraIngestSession, capability.session_id)
    if session is None or session.timeline_id != capability.timeline_id:
        raise HTTPException(status_code=404, detail="Wireless camera session not found")
    if session.status != "capturing":
        raise HTTPException(status_code=409, detail="Wireless camera session is no longer accepting a recording")
    metadata = dict(session.metadata_json or {}); wire = dict(metadata.get("wireless_multicam") or {})
    origin_ms = int(wire["capture_origin_ms"])
    now_ms = int(time.time() * 1000)
    # Reject wildly incorrect client clocks while retaining sub-second pairing accuracy.
    started_ms = payload.server_aligned_started_at_ms if abs(payload.server_aligned_started_at_ms - now_ms) <= 60_000 else now_ms
    offset = max(0.0, (started_ms - origin_ms) / 1000)
    wire.update({"timeline_offset_seconds": round(offset, 3), "started_at_ms": started_ms})
    session.metadata_json, session.started_at = {**metadata, "wireless_multicam": wire}, datetime.fromtimestamp(started_ms / 1000, tz=UTC)
    timeline = db.get(Timeline, capability.timeline_id)
    if timeline:
        settings = dict(timeline.settings_json or {}); multi = dict(settings.get("wireless_multicam") or {})
        multi["cameras"] = [{**item, "status": "capturing"} if item.get("pairing_id") == str(pairing_id) else item for item in multi.get("cameras", [])]
        timeline.settings_json = {**settings, "wireless_multicam": multi}
    db.commit()
    return WirelessCameraStartResponse(session_id=session.id, timeline_offset_seconds=round(offset, 3), capture_origin_ms=origin_ms)


@mobile_router.get("/pairings/{pairing_id}/clock", response_model=WirelessCameraClockResponse)
def read_pairing_clock(pairing_id: UUID, x_wireless_camera_token: str | None = Header(default=None), db: Session = Depends(get_db)) -> WirelessCameraClockResponse:
    capability = _token_or_401(x_wireless_camera_token, pairing_id)
    session = db.get(CameraIngestSession, capability.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Wireless camera session not found")
    wire = (session.metadata_json or {}).get("wireless_multicam") or {}
    return WirelessCameraClockResponse(server_epoch_ms=int(time.time() * 1000), capture_origin_ms=int(wire["capture_origin_ms"]))


@mobile_router.put("/pairings/{pairing_id}/chunks/{sequence_number}", response_model=WirelessCameraChunkResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_recording_chunk(pairing_id: UUID, sequence_number: int, request: Request, x_wireless_camera_token: str | None = Header(default=None), db: Session = Depends(get_db)) -> WirelessCameraChunkResponse:
    if sequence_number < 0:
        raise HTTPException(status_code=422, detail="sequence_number must be >= 0")
    capability = _token_or_401(x_wireless_camera_token, pairing_id)
    session = db.get(CameraIngestSession, capability.session_id)
    if session is None or session.status != "capturing":
        raise HTTPException(status_code=409, detail="Wireless camera is not recording")
    existing = db.scalar(select(CameraIngestChunk).where(CameraIngestChunk.session_id == session.id, CameraIngestChunk.sequence_number == sequence_number))
    if existing:
        return WirelessCameraChunkResponse(chunk_id=existing.id, sequence_number=sequence_number, status=existing.status, duplicate=True)
    content_type = request.headers.get("content-type", "video/webm").split(";", 1)[0].lower()
    if not content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="Wireless camera chunks must be video")
    suffix = ".webm" if "webm" in content_type else ".mp4"
    fd, temporary_name = tempfile.mkstemp(prefix="wireless-camera-", suffix=suffix); os.close(fd)
    temporary_path, total_bytes, digest = Path(temporary_name), 0, hashlib.sha256()
    try:
        with temporary_path.open("wb") as output:
            async for data in request.stream():
                total_bytes += len(data)
                if total_bytes > settings.ingest_max_chunk_bytes:
                    raise HTTPException(status_code=413, detail="Wireless camera chunk is too large")
                digest.update(data); output.write(data)
        if not total_bytes:
            raise HTTPException(status_code=422, detail="Wireless camera chunk is empty")
        storage_key = f"projects/{session.project_id}/wireless-cameras/{pairing_id}/source/{sequence_number:09d}-{digest.hexdigest()[:12]}{suffix}"
        await run_in_threadpool(upload_object, storage_key, str(temporary_path), content_type)
        chunk = CameraIngestChunk(session_id=session.id, sequence_number=sequence_number, storage_key=storage_key,
            content_sha256=digest.hexdigest(), mime_type=content_type, size_bytes=total_bytes,
            camera_metadata_json={"wireless_pairing_id": str(pairing_id)})
        db.add(chunk); db.commit(); db.refresh(chunk)
        process_camera_ingest_chunk.delay(str(chunk.id))
        return WirelessCameraChunkResponse(chunk_id=chunk.id, sequence_number=sequence_number, status=chunk.status)
    finally:
        temporary_path.unlink(missing_ok=True)


@mobile_router.post("/pairings/{pairing_id}/complete", response_model=WirelessCameraCompleteResponse)
def complete_recording(pairing_id: UUID, x_wireless_camera_token: str | None = Header(default=None), db: Session = Depends(get_db)) -> WirelessCameraCompleteResponse:
    capability = _token_or_401(x_wireless_camera_token, pairing_id)
    session = db.get(CameraIngestSession, capability.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Wireless camera session not found")
    session.status, session.ended_at = "completed", datetime.now(UTC)
    metadata = dict(session.metadata_json or {}); offset = float(metadata.get("wireless_multicam", {}).get("timeline_offset_seconds", 0))
    timeline = db.get(Timeline, capability.timeline_id)
    if timeline:
        settings = dict(timeline.settings_json or {}); multi = dict(settings.get("wireless_multicam") or {})
        multi["cameras"] = [{**item, "status": "completed"} if item.get("pairing_id") == str(pairing_id) else item for item in multi.get("cameras", [])]
        timeline.settings_json = {**settings, "wireless_multicam": multi}
    db.commit()
    return WirelessCameraCompleteResponse(session_id=session.id, status="completed", timeline_offset_seconds=offset)


@mobile_router.websocket("/pairings/{pairing_id}/signal")
async def relay_webrtc_signalling(websocket: WebSocket, pairing_id: UUID, token: str, role: str) -> None:
    if role not in {"editor", "mobile"}:
        await websocket.close(code=1008); return
    try:
        verify_wireless_camera_token(token, pairing_id)
    except WirelessCameraTokenError:
        await websocket.close(code=1008); return
    await websocket.accept()
    await wireless_signalling.connect(pairing_id, role, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict) or message.get("type") not in {"offer", "answer", "candidate", "hangup"}:
                continue
            await wireless_signalling.relay(pairing_id, role, message)
    except WebSocketDisconnect:
        pass
    finally:
        await wireless_signalling.disconnect(pairing_id, role, websocket)
