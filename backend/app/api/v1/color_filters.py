import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline, User
from app.schemas.color_filters import (
    ApplyColorFilterRequest,
    ApplyColorFilterResponse,
    ColorFilterPresetListResponse,
    CreateColorMatchRequest,
    CreateColorMatchResponse,
)
from app.services.lut_generation import LUTGenerationError, extract_color_match, extract_video_reference_frame, generate_color_match_cube, write_style_profile
from app.services.preset_luts import get_preset_lut, preset_catalog, preset_lut_cube
from app.services.storage import create_download_url, download_object, upload_bytes, upload_object


router = APIRouter(prefix="/timelines", tags=["color-filters"])


@router.get("/color-filter-presets", response_model=ColorFilterPresetListResponse)
def list_color_filter_presets() -> ColorFilterPresetListResponse:
    """Public catalogue only; applying a look remains timeline-owner scoped."""
    return ColorFilterPresetListResponse(presets=preset_catalog())


@router.put("/{timeline_id}/color-filter", response_model=ApplyColorFilterResponse)
def apply_color_filter(
    timeline_id: UUID, payload: ApplyColorFilterRequest, db: Session = Depends(get_db)
) -> ApplyColorFilterResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    try:
        preset = get_preset_lut(payload.preset_id)
        cube = preset_lut_cube(preset.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Keep source files immutable and safely overwrite only this timeline's current preset object.
    lut_key = f"projects/{timeline.project_id}/timelines/{timeline.id}/preset-luts/{preset.id}.cube"
    upload_bytes(lut_key, cube.encode("utf-8"), "text/plain; charset=utf-8")
    color_lut: dict[str, object] = {
        "preset_id": preset.id,
        "display_name": preset.name,
        "lut_key": lut_key,
        "intensity": round(payload.intensity / 100, 4),
    }
    timeline.settings_json = {**dict(timeline.settings_json or {}), "color_lut": color_lut}
    db.commit()
    return ApplyColorFilterResponse(timeline_id=timeline.id, status="configured", color_lut=color_lut)


def _timeline_source_asset_id(timeline: Timeline) -> str | None:
    document = dict(timeline.settings_json or {}).get("confirmed_timeline", {})
    if not isinstance(document, dict):
        return None
    if document.get("source_asset_id"):
        return str(document["source_asset_id"])
    for track in document.get("tracks", []):
        if isinstance(track, dict) and track.get("type") == "main_video":
            clips = track.get("clips", [])
            if clips and isinstance(clips[0], dict) and clips[0].get("source_asset_id"):
                return str(clips[0]["source_asset_id"])
    return None


@router.post("/{timeline_id}/color-match", response_model=CreateColorMatchResponse)
def create_color_match(
    timeline_id: UUID, payload: CreateColorMatchRequest, db: Session = Depends(get_db)
) -> CreateColorMatchResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    reference = db.get(MediaAsset, payload.reference_image_asset_id)
    source_id = payload.source_asset_id or _timeline_source_asset_id(timeline)
    source = db.get(MediaAsset, UUID(str(source_id))) if source_id else None
    if reference is None or reference.project_id != timeline.project_id or reference.status != MediaStatus.READY or reference.media_type != MediaType.IMAGE:
        raise HTTPException(status_code=422, detail="Reference image must be a ready image asset in this project")
    if source is None or source.project_id != timeline.project_id or source.status != MediaStatus.READY or source.media_type != MediaType.VIDEO:
        raise HTTPException(status_code=422, detail="Source must be a ready video asset in this project")
    try:
        with tempfile.TemporaryDirectory(prefix=f"color-match-{timeline.id}-") as temporary:
            workdir = Path(temporary); reference_path = workdir / "reference-image"; source_proxy = workdir / "source.mp4"; source_frame = workdir / "source-frame.jpg"
            download_object(reference.storage_key, str(reference_path)); download_object(source.proxy_key or source.storage_key, str(source_proxy))
            extract_video_reference_frame(source_proxy, source_frame, seek_seconds=min(.5, max(0.0, float(source.duration_seconds or 0) / 2)))
            profile = extract_color_match(source_frame, reference_path)
            cube_path, profile_path = workdir / "color-match.cube", workdir / "color-match-profile.json"
            generate_color_match_cube(profile, cube_path, size=payload.lut_size); write_style_profile(profile, profile_path)
            base = f"projects/{timeline.project_id}/timelines/{timeline.id}/color-match/{reference.id}"
            lut_key, profile_key = f"{base}.cube", f"{base}.json"
            upload_object(lut_key, str(cube_path), "text/plain; charset=utf-8"); upload_object(profile_key, str(profile_path), "application/json")
    except (LUTGenerationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    color_lut: dict[str, object] = {
        "kind": "color_match", "display_name": f"參考圖色彩匹配：{reference.filename}", "lut_key": lut_key,
        "profile_key": profile_key, "reference_asset_id": str(reference.id), "source_asset_id": str(source.id), "intensity": round(payload.intensity / 100, 4),
    }
    timeline.settings_json = {**dict(timeline.settings_json or {}), "color_lut": color_lut}; db.commit()
    return CreateColorMatchResponse(timeline_id=timeline.id, status="configured", color_lut=color_lut, lut_download_url=create_download_url(lut_key))
