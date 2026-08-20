"""Secure camera-to-cloud chunk ingestion.

TLS is terminated by the edge proxy.  Each camera still signs every request so a
leaked network path, presigned URL, or internal proxy hop cannot write media.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import CameraDevice, CameraIngestChunk, CameraIngestSession, Project, Timeline, User
from app.schemas.camera_ingest import (
    CameraChunkAcceptedResponse,
    CameraIngestSessionResponse,
    CompleteCameraIngestResponse,
    RegisterCameraDeviceRequest,
    RegisterCameraDeviceResponse,
    StartCameraIngestRequest,
)
from app.services.camera_ingest_security import (
    CameraIngestSecurityError,
    decode_camera_metadata,
    decrypt_device_secret,
    encrypt_device_secret,
    verify_chunk_signature,
)
from app.services.storage import upload_object
from app.tasks.ingest_tasks import process_camera_ingest_chunk


router = APIRouter(prefix="/camera-ingest", tags=["camera-ingest"])


def require_ingest_management_token(value: str | None = Header(default=None, alias="X-Ingest-Management-Token")) -> None:
    """Temporary control-plane guard until the main user/device authorization layer is wired in."""
    if not settings.ingest_management_token:
        raise HTTPException(status_code=503, detail="Camera ingest management is not configured")
    if not value or not hmac.compare_digest(value, settings.ingest_management_token):
        raise HTTPException(status_code=403, detail="Camera ingest management authorization failed")


def _session_response(session: CameraIngestSession) -> CameraIngestSessionResponse:
    return CameraIngestSessionResponse(
        session_id=session.id,
        project_id=session.project_id,
        device_id=session.device_id,
        timeline_id=session.timeline_id,
        capture_id=session.capture_id,
        status=session.status,
        upload_url_template=f"/api/v1/camera-ingest/sessions/{session.id}/chunks/{{sequence_number}}",
        hmac_headers=["X-Device-Id", "X-Ingest-Timestamp", "X-Ingest-Nonce", "X-Chunk-SHA256", "X-Chunk-Signature", "X-Camera-Metadata"],
    )


def _require_tls(request: Request) -> None:
    if not settings.ingest_require_tls:
        return
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    scheme = forwarded or request.url.scheme.lower()
    if scheme != "https":
        raise HTTPException(status_code=426, detail="Camera ingest requires HTTPS/TLS")


@router.post("/devices", response_model=RegisterCameraDeviceResponse, status_code=status.HTTP_201_CREATED)
def register_camera_device(
    payload: RegisterCameraDeviceRequest,
    _: None = Depends(require_ingest_management_token),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RegisterCameraDeviceResponse:
    # Two-factor control plane: the management token proves this caller may
    # provision cameras at all; current_user (verified bearer token) proves
    # which specific user's project is being provisioned for. A
    # client-supplied user_id field is never trusted for the latter.
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User does not own this project")
    existing = db.scalar(select(CameraDevice).where(CameraDevice.project_id == payload.project_id, CameraDevice.device_identifier == payload.device_identifier))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Camera device identifier is already registered for this project")
    raw_secret = secrets.token_urlsafe(32)
    try:
        encrypted_secret = encrypt_device_secret(raw_secret)
    except CameraIngestSecurityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    device = CameraDevice(
        project_id=payload.project_id,
        device_identifier=payload.device_identifier,
        display_name=payload.display_name,
        device_type=payload.device_type,
        encrypted_hmac_secret=encrypted_secret,
        metadata_json=payload.metadata.model_dump(mode="json", exclude_none=True),
    )
    db.add(device); db.commit(); db.refresh(device)
    return RegisterCameraDeviceResponse(device_id=device.id, device_identifier=device.device_identifier, device_secret=raw_secret)


@router.post("/devices/{device_id}/sessions", response_model=CameraIngestSessionResponse, status_code=status.HTTP_201_CREATED)
def start_camera_ingest_session(
    device_id: UUID,
    payload: StartCameraIngestRequest,
    _: None = Depends(require_ingest_management_token),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CameraIngestSessionResponse:
    device = db.get(CameraDevice, device_id)
    if device is None or not device.is_active:
        raise HTTPException(status_code=404, detail="Active camera device not found")
    project = db.get(Project, device.project_id)
    if project is None or project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User does not own this camera project")
    existing = db.scalar(select(CameraIngestSession).where(CameraIngestSession.device_id == device_id, CameraIngestSession.capture_id == payload.capture_id))
    if existing is not None:
        return _session_response(existing)
    if payload.timeline_id:
        timeline = db.get(Timeline, payload.timeline_id)
        if timeline is None or timeline.project_id != project.id:
            raise HTTPException(status_code=404, detail="Timeline not found in camera project")
    else:
        latest = db.scalar(select(Timeline.version).where(Timeline.project_id == project.id).order_by(Timeline.version.desc()).limit(1))
        timeline = Timeline(project_id=project.id, name=f"Live ingest · {payload.capture_id}", version=int(latest or 0) + 1, is_current=False, settings_json={"live_ingest": {"status": "capturing", "clips": []}})
        db.add(timeline); db.flush()
    session = CameraIngestSession(
        project_id=project.id, device_id=device.id, timeline_id=timeline.id, capture_id=payload.capture_id,
        metadata_json=payload.metadata.model_dump(mode="json", exclude_none=True),
    )
    db.add(session); db.commit(); db.refresh(session)
    return _session_response(session)


@router.put("/sessions/{session_id}/chunks/{sequence_number}", response_model=CameraChunkAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_camera_chunk(
    session_id: UUID,
    sequence_number: int,
    request: Request,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_ingest_timestamp: str | None = Header(default=None, alias="X-Ingest-Timestamp"),
    x_ingest_nonce: str | None = Header(default=None, alias="X-Ingest-Nonce"),
    x_chunk_sha256: str | None = Header(default=None, alias="X-Chunk-SHA256"),
    x_chunk_signature: str | None = Header(default=None, alias="X-Chunk-Signature"),
    x_camera_metadata: str | None = Header(default=None, alias="X-Camera-Metadata"),
    db: Session = Depends(get_db),
) -> CameraChunkAcceptedResponse:
    if sequence_number < 0:
        raise HTTPException(status_code=422, detail="sequence_number must be >= 0")
    _require_tls(request)
    session = db.get(CameraIngestSession, session_id)
    if session is None or session.status != "capturing":
        raise HTTPException(status_code=409, detail="Camera ingest session is not accepting chunks")
    if not x_device_id or str(session.device_id) != x_device_id:
        raise HTTPException(status_code=403, detail="Camera device does not own this ingest session")
    device = db.get(CameraDevice, session.device_id)
    if device is None or not device.is_active:
        raise HTTPException(status_code=403, detail="Camera device is inactive")
    try:
        metadata, metadata_hash = decode_camera_metadata(x_camera_metadata)
        verify_chunk_signature(
            device_secret=decrypt_device_secret(device.encrypted_hmac_secret), device_id=x_device_id,
            path=request.url.path, session_id=str(session_id), sequence_number=sequence_number,
            timestamp=x_ingest_timestamp, nonce=x_ingest_nonce, content_sha256=x_chunk_sha256,
            signature=x_chunk_signature, metadata_hash=metadata_hash,
        )
    except CameraIngestSecurityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    existing = db.scalar(select(CameraIngestChunk).where(CameraIngestChunk.session_id == session_id, CameraIngestChunk.sequence_number == sequence_number))
    if existing is not None:
        if existing.content_sha256.lower() != (x_chunk_sha256 or "").lower():
            raise HTTPException(status_code=409, detail="Chunk sequence already exists with a different SHA-256")
        return CameraChunkAcceptedResponse(chunk_id=existing.id, sequence_number=sequence_number, status=existing.status, duplicate=True)
    content_type = request.headers.get("content-type", "video/mp4").split(";", 1)[0].lower()
    if not (content_type.startswith("video/") or content_type == "application/octet-stream"):
        raise HTTPException(status_code=415, detail="Camera chunk must have a video content type")

    fd, temporary_name = tempfile.mkstemp(prefix="camera-chunk-", suffix=".bin")
    os.close(fd)
    temporary_path = Path(temporary_name)
    total_bytes = 0
    digest = hashlib.sha256()
    try:
        with temporary_path.open("wb") as output:
            async for data in request.stream():
                total_bytes += len(data)
                if total_bytes > settings.ingest_max_chunk_bytes:
                    raise HTTPException(status_code=413, detail="Camera chunk exceeds configured size limit")
                digest.update(data); output.write(data)
        if total_bytes == 0:
            raise HTTPException(status_code=422, detail="Camera chunk body is empty")
        if not hmac.compare_digest(digest.hexdigest(), (x_chunk_sha256 or "").lower()):
            raise HTTPException(status_code=422, detail="Camera chunk SHA-256 does not match body")
        storage_key = f"projects/{session.project_id}/camera-ingest/{session.id}/source/{sequence_number:09d}-{digest.hexdigest()[:12]}.mp4"
        await run_in_threadpool(upload_object, storage_key, str(temporary_path), content_type)
        chunk = CameraIngestChunk(
            session_id=session.id, sequence_number=sequence_number, storage_key=storage_key,
            content_sha256=digest.hexdigest(), mime_type=content_type, size_bytes=total_bytes,
            camera_metadata_json=metadata.model_dump(mode="json", exclude_none=True),
        )
        db.add(chunk); db.commit(); db.refresh(chunk)
        try:
            process_camera_ingest_chunk.delay(str(chunk.id))
        except Exception as exc:
            chunk.status, chunk.error_message = "failed", f"Worker queue unavailable: {exc}"
            db.commit()
            raise HTTPException(status_code=503, detail="Chunk persisted but worker queue is unavailable") from exc
        return CameraChunkAcceptedResponse(chunk_id=chunk.id, sequence_number=sequence_number, status=chunk.status)
    finally:
        temporary_path.unlink(missing_ok=True)


@router.post("/sessions/{session_id}/complete", response_model=CompleteCameraIngestResponse)
def complete_camera_ingest_session(session_id: UUID, _: None = Depends(require_ingest_management_token), db: Session = Depends(get_db)) -> CompleteCameraIngestResponse:
    session = db.get(CameraIngestSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Camera ingest session not found")
    session.status = "completed"
    session.ended_at = datetime.now(UTC)
    db.commit()
    return CompleteCameraIngestResponse(session_id=session.id, status=session.status, total_duration_seconds=float(session.total_duration_seconds or 0))
