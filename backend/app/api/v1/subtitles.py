from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import MediaAsset, Timeline, User
from app.schemas.subtitle import CaptionStyleRequest, GenerateBilingualSubtitlesRequest, GenerateSubtitlesRequest, SubtitleCue, SubtitleExportResponse, SubtitleGenerationResponse
from app.services.storage import create_download_url, upload_bytes
from app.services.subtitles import cues_to_ass
from app.services.bilingual_subtitles import bilingual_to_ass
from app.tasks.subtitle_tasks import generate_bilingual_subtitles_for_timeline, generate_subtitles_for_timeline

router = APIRouter(prefix="/timelines", tags=["subtitles"])


@router.post("/{timeline_id}/generate-subtitles", response_model=SubtitleGenerationResponse, status_code=status.HTTP_202_ACCEPTED)
def request_subtitle_generation(
    timeline_id: UUID,
    payload: GenerateSubtitlesRequest,
    db: Session = Depends(get_db),
) -> SubtitleGenerationResponse:
    timeline = db.get(Timeline, timeline_id)
    asset = db.get(MediaAsset, payload.source_asset_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if asset is None or asset.project_id != timeline.project_id:
        raise HTTPException(status_code=400, detail="Source asset does not belong to this timeline project")
    if not any(segment.action == "keep" for segment in payload.segments):
        raise HTTPException(status_code=422, detail="At least one keep segment is required")

    timeline.settings_json = {
        **timeline.settings_json,
        "confirmed_timeline": {
            "source_asset_id": str(payload.source_asset_id),
            "segments": [segment.model_dump(mode="json") for segment in payload.segments],
            "language": payload.language,
        },
        "subtitles": {"status": "queued", "target_language": payload.target_language},
    }
    db.commit()
    task = generate_subtitles_for_timeline.delay(str(timeline.id))
    return SubtitleGenerationResponse(task_id=task.id, timeline_id=timeline.id, status="queued")


@router.post("/{timeline_id}/generate-bilingual-subtitles", response_model=SubtitleGenerationResponse, status_code=status.HTTP_202_ACCEPTED)
def request_bilingual_subtitle_generation(
    timeline_id: UUID, payload: GenerateBilingualSubtitlesRequest, db: Session = Depends(get_db),
) -> SubtitleGenerationResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    if dict(timeline.settings_json.get("subtitles", {})).get("status") != "completed":
        raise HTTPException(status_code=409, detail="Generate source subtitles before translating")
    task = generate_bilingual_subtitles_for_timeline.delay(str(timeline.id), payload.target_language, payload.source_language)
    return SubtitleGenerationResponse(task_id=task.id, timeline_id=timeline.id, status="queued")


@router.get("/{timeline_id}/bilingual-subtitles/export", response_model=SubtitleExportResponse)
def get_bilingual_subtitle_export(
    timeline_id: UUID, user_id: UUID, format: Literal["srt", "vtt"], track: Literal["bilingual", "source", "target"],
    db: Session = Depends(get_db),
) -> SubtitleExportResponse:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot access this timeline")
    bilingual = dict(timeline.settings_json.get("bilingual_subtitles") or {})
    key = bilingual.get(f"{track}_{format}_key")
    if bilingual.get("status") != "completed" or not key:
        raise HTTPException(status_code=404, detail="Requested bilingual subtitle export is unavailable")
    language = bilingual.get("target_language") if track == "target" else bilingual.get("source_language") if track == "source" else None
    return SubtitleExportResponse(format=format, track=track, language=language, download_url=create_download_url(str(key), attachment_filename=f"{timeline.id}-{track}.{format}"))


@router.put("/{timeline_id}/caption-style")
def update_caption_style(timeline_id: UUID, payload: CaptionStyleRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    subtitles = dict(timeline.settings_json.get("subtitles", {}))
    if subtitles.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Generate subtitles before applying a caption style")
    cues = [SubtitleCue.model_validate(item) for item in subtitles.get("items", [])]
    if not cues:
        raise HTTPException(status_code=422, detail="No subtitle cues available")
    bilingual = dict(timeline.settings_json.get("bilingual_subtitles") or {})
    ass_key = f"projects/{timeline.project_id}/timelines/{timeline.id}/subtitles/{'bilingual/' + str(bilingual.get('target_language')) + '/' if bilingual.get('status') == 'completed' else ''}{payload.preset}.ass"
    if bilingual.get("status") == "completed":
        ass = bilingual_to_ass(cues, list(bilingual.get("items", [])), preset=payload.preset, aspect_ratio=payload.aspect_ratio)
        bilingual = {**bilingual, "ass_key": ass_key, "caption_preset": payload.preset, "caption_aspect_ratio": payload.aspect_ratio}
    else:
        ass = cues_to_ass(cues, preset=payload.preset, aspect_ratio=payload.aspect_ratio)
    upload_bytes(ass_key, ass.encode("utf-8"), "text/x-ssa")
    timeline.settings_json = {
        **timeline.settings_json,
        "caption_style": payload.model_dump(mode="json"),
        "subtitles": {**subtitles, "ass_key": ass_key, "caption_preset": payload.preset, "caption_aspect_ratio": payload.aspect_ratio, "render_mode": "ass"},
        "bilingual_subtitles": bilingual if bilingual.get("status") == "completed" else timeline.settings_json.get("bilingual_subtitles"),
    }
    db.commit()
    return {"timeline_id": str(timeline.id), "status": "configured", "preset": payload.preset, "aspect_ratio": payload.aspect_ratio, "ass_key": ass_key}
