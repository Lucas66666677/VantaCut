"""Signalling and control API for the low-latency AI live director."""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.websocket import authenticate_websocket_bearer
from app.core.config import settings
from app.db.session import get_db
from app.models.entities import Project, User
from app.schemas.live import (
    AttachGatewaySourceRequest,
    CreateLiveSessionRequest,
    LiveCaptionRequest,
    LiveDirectorOverride,
    LiveSessionResponse,
    LiveSessionStatus,
    WebRTCAnswerResponse,
    WebRTCOfferRequest,
)
from app.services.live_director import LiveDirectorError, live_directors

router = APIRouter(prefix="/live", tags=["live-director"])


def _owned_project(project_id: UUID, user: User, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot operate this project's live session")
    return project


def _director_or_404(session_id: str):
    try:
        return live_directors.get(session_id)
    except LiveDirectorError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _owned_director(session_id: str, user: User, db: Session):
    director = _director_or_404(session_id)
    try:
        project_id = UUID(director.project_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Live session project is invalid") from exc
    _owned_project(project_id, user, db)
    return director


@router.post("/sessions", response_model=LiveSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_live_session(payload: CreateLiveSessionRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LiveSessionResponse:
    _owned_project(payload.project_id, current_user, db)
    session_id = str(uuid4())
    director = live_directors.create(
        session_id=session_id,
        project_id=str(payload.project_id),
        width=payload.width,
        height=payload.height,
        fps=payload.fps,
        output_rtmp_url=payload.output_rtmp_url,
        wide_camera_id=payload.wide_camera_id,
    )
    try:
        await director.start()
    except Exception as exc:
        director.status = "failed"
        raise HTTPException(status_code=502, detail=f"Unable to start RTMP program output: {exc}") from exc

    base = settings.live_mediamtx_public_rtmp_base_url.rstrip("/")
    return LiveSessionResponse(
        session_id=session_id,
        project_id=payload.project_id,
        status=director.status,
        obs_publish_url_template=f"{base}/live/{session_id}/{{camera_id}}",
        phone_offer_path_template=f"/api/v1/live/sessions/{session_id}/webrtc/offer",
        attach_gateway_source_path=f"/api/v1/live/sessions/{session_id}/sources/gateway",
        control_websocket_path=f"/api/v1/live/sessions/{session_id}/control/ws",
    )


@router.post("/sessions/{session_id}/webrtc/offer", response_model=WebRTCAnswerResponse)
async def accept_mobile_webrtc_offer(session_id: str, payload: WebRTCOfferRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> WebRTCAnswerResponse:
    director = _owned_director(session_id, current_user, db)
    try:
        answer = await director.add_websocket_offer(payload.camera_id, payload.sdp, payload.is_wide_camera)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"WebRTC negotiation failed: {exc}") from exc
    return WebRTCAnswerResponse(sdp=answer.sdp, type="answer")


@router.post("/sessions/{session_id}/sources/gateway", response_model=LiveSessionStatus)
async def attach_obs_or_gateway_source(session_id: str, payload: AttachGatewaySourceRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LiveSessionStatus:
    director = _owned_director(session_id, current_user, db)
    # Deliberately derive, rather than accept, a source URL.  This is both a
    # tenancy boundary and an SSRF boundary for a media parser process.
    rtsp_base = settings.live_mediamtx_internal_rtsp_base_url.rstrip("/")
    stream_path = f"live/{session_id}/{payload.camera_id}"
    try:
        await director.attach_gateway_source(payload.camera_id, f"{rtsp_base}/{stream_path}", payload.is_wide_camera)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cannot subscribe to MediaMTX source {stream_path}: {exc}") from exc
    return LiveSessionStatus.model_validate(director.snapshot())


@router.post("/sessions/{session_id}/captions", response_model=LiveSessionStatus)
async def add_live_caption(session_id: str, payload: LiveCaptionRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LiveSessionStatus:
    director = _owned_director(session_id, current_user, db)
    director.set_caption(payload.text, payload.emotion, payload.animation_preset, payload.ttl_seconds)
    return LiveSessionStatus.model_validate(director.snapshot())


@router.post("/sessions/{session_id}/director", response_model=LiveSessionStatus)
async def override_live_director(session_id: str, payload: LiveDirectorOverride, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LiveSessionStatus:
    director = _owned_director(session_id, current_user, db)
    if payload.camera_id and payload.camera_id not in director.sources:
        raise HTTPException(status_code=422, detail="camera_id is not attached to this session")
    director.set_override(payload.layout, payload.camera_id)
    return LiveSessionStatus.model_validate(director.snapshot())


@router.get("/sessions/{session_id}", response_model=LiveSessionStatus)
async def get_live_session(session_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LiveSessionStatus:
    return LiveSessionStatus.model_validate(_owned_director(session_id, current_user, db).snapshot())


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def stop_live_session(session_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    _owned_director(session_id, current_user, db)
    try:
        await live_directors.stop(session_id)
    except LiveDirectorError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.websocket("/sessions/{session_id}/control/ws")
async def live_control_events(websocket: WebSocket, session_id: str, db: Session = Depends(get_db)) -> None:
    """Director telemetry for a control-room UI; command writes remain authenticated HTTP APIs."""
    user = await authenticate_websocket_bearer(websocket, db)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        director = _owned_director(session_id, user, db)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept(subprotocol="bearer")
    try:
        while True:
            await websocket.send_json(director.snapshot())
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        return
