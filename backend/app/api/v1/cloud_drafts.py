from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.entities import MediaAsset, Timeline, User
from app.schemas.cloud_drafts import (
    CloudDraftPayload,
    CloudDraftResponse,
    MobilePreviewAsset,
    MobilePreviewHandoffRequest,
    MobilePreviewHandoffResponse,
    MobilePreviewManifest,
)
from app.services.mobile_handoff import (
    MobileHandoffTokenError,
    issue_mobile_handoff_token,
    qr_code_data_uri,
    verify_mobile_handoff_token,
)
from app.services.storage import create_download_url


router = APIRouter(prefix="/timelines", tags=["cloud-drafts"])


def _owned_timeline(timeline_id: UUID, user_id: UUID, db: Session) -> Timeline:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot access this timeline")
    return timeline


def _draft_from_settings(timeline: Timeline) -> dict[str, object]:
    return dict((timeline.settings_json or {}).get("cloud_draft") or {})


@router.put("/{timeline_id}/cloud-draft", response_model=CloudDraftResponse)
def save_cloud_draft(timeline_id: UUID, payload: CloudDraftPayload, db: Session = Depends(get_db)) -> CloudDraftResponse:
    timeline = _owned_timeline(timeline_id, payload.user_id, db)
    encoded = json.dumps(payload.timeline, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > settings.cloud_draft_max_bytes:
        raise HTTPException(status_code=413, detail="Timeline draft exceeds the configured cloud draft size limit")
    updated_at = datetime.now(UTC)
    draft = {
        "schema": "com.aivideo.cloud-draft.v1", "timeline": payload.timeline,
        "editor_state": payload.editor_state, "updated_at": updated_at.isoformat(),
        "client_updated_at": payload.client_updated_at.isoformat() if payload.client_updated_at else None,
    }
    timeline.settings_json = {**dict(timeline.settings_json or {}), "cloud_draft": draft}
    db.commit()
    return CloudDraftResponse(timeline_id=timeline.id, status="saved", timeline=payload.timeline, editor_state=payload.editor_state, updated_at=updated_at)


@router.get("/{timeline_id}/cloud-draft", response_model=CloudDraftResponse)
def load_cloud_draft(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> CloudDraftResponse:
    timeline = _owned_timeline(timeline_id, user_id, db)
    draft = _draft_from_settings(timeline)
    if not draft:
        raise HTTPException(status_code=404, detail="No cloud draft exists for this timeline")
    raw_updated_at = draft.get("updated_at")
    return CloudDraftResponse(
        timeline_id=timeline.id, status="loaded", timeline=dict(draft.get("timeline") or {}),
        editor_state=dict(draft.get("editor_state") or {}),
        updated_at=datetime.fromisoformat(str(raw_updated_at)) if raw_updated_at else None,
    )


@router.post("/{timeline_id}/mobile-preview-handoff", response_model=MobilePreviewHandoffResponse)
def create_mobile_preview_handoff(
    timeline_id: UUID, payload: MobilePreviewHandoffRequest, db: Session = Depends(get_db)
) -> MobilePreviewHandoffResponse:
    _owned_timeline(timeline_id, payload.user_id, db)
    token, expires_epoch = issue_mobile_handoff_token(timeline_id)
    preview_url = f"{settings.web_app_base_url.rstrip('/')}/mobile-preview?token={token}"
    return MobilePreviewHandoffResponse(
        preview_url=preview_url, qr_code_data_uri=qr_code_data_uri(preview_url),
        expires_at=datetime.fromtimestamp(expires_epoch, tz=UTC),
    )


@router.get("/mobile-preview/{token}", response_model=MobilePreviewManifest)
def get_mobile_preview_manifest(token: str, db: Session = Depends(get_db)) -> MobilePreviewManifest:
    try:
        timeline_id = verify_mobile_handoff_token(token)
    except MobileHandoffTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    draft = _draft_from_settings(timeline)
    document = dict(draft.get("timeline") or {})
    if not document:
        raise HTTPException(status_code=409, detail="Save a cloud draft before opening mobile preview")
    source_ids: set[str] = set()
    for clip in list(document.get("clips") or []):
        if isinstance(clip, dict) and clip.get("source_asset_id"):
            source_ids.add(str(clip["source_asset_id"]))
    if document.get("source_asset_id"):
        source_ids.add(str(document["source_asset_id"]))
    confirmed = dict((timeline.settings_json or {}).get("confirmed_timeline") or {})
    if confirmed.get("source_asset_id"):
        source_ids.add(str(confirmed["source_asset_id"]))
    if not source_ids:
        raise HTTPException(status_code=409, detail="Timeline preview requires a source media asset")
    try:
        asset_ids = [UUID(value) for value in source_ids]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Timeline contains an invalid source asset ID") from exc
    assets = db.scalars(select(MediaAsset).where(MediaAsset.project_id == timeline.project_id, MediaAsset.id.in_(asset_ids))).all()
    by_id = {str(asset.id): asset for asset in assets}
    if len(by_id) != len(source_ids):
        raise HTTPException(status_code=409, detail="Timeline contains an unavailable preview asset")
    expires_at = int(token.split(".", 3)[1])
    remaining = max(60, expires_at - int(datetime.now(UTC).timestamp()))
    return MobilePreviewManifest(
        timeline_id=timeline.id, expires_at=datetime.fromtimestamp(expires_at, tz=UTC), timeline=document,
        assets=[MobilePreviewAsset(id=asset_id, url=create_download_url(asset.proxy_key or asset.storage_key, expires_in=remaining)) for asset_id, asset in by_id.items()],
    )
