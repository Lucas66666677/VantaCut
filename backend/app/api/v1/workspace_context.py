"""Small, explainable workspace-mode classifier over already-computed project signals."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import AIAnalysis, AnalysisType, Clip, Timeline, User
from app.schemas.workspace_context import WorkspaceContextResponse

router = APIRouter(prefix="/timelines", tags=["workspace-context"])


@router.get("/{timeline_id}/clips/{clip_id}/workspace-context", response_model=WorkspaceContextResponse)
def get_workspace_context(timeline_id: UUID, clip_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> WorkspaceContextResponse:
    timeline, clip = db.get(Timeline, timeline_id), db.get(Clip, clip_id)
    if timeline is None or clip is None or clip.timeline_id != timeline.id:
        raise HTTPException(status_code=404, detail="Timeline clip not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot view this workspace")

    analyses = db.query(AIAnalysis).filter(AIAnalysis.media_asset_id == clip.source_asset_id, AIAnalysis.status == "completed").all()
    kinds = {item.analysis_type.value if hasattr(item.analysis_type, "value") else str(item.analysis_type) for item in analyses}
    tags = {str(value).lower() for value in (clip.source_asset.metadata_json or {}).get("semantic_tags", [])}
    settings = dict(timeline.settings_json or {})
    mechanical = dict(settings.get("mechanical_ar") or {})
    screen_focus = dict(settings.get("screen_focus") or {})

    steam_words = {"code", "coding", "ide", "circuit", "electronics", "breadboard", "microcontroller", "robot", "tinkercad", "arduino", "程式", "電路", "接線", "機器人"}
    landscape_words = {"landscape", "travel", "nature", "mountain", "ocean", "forest", "sunset", "cityscape", "風景", "旅行", "山", "海", "雪"}
    steam_score = (0.72 if AnalysisType.SCREEN_FOCUS.value in kinds else 0) + (0.68 if str(mechanical.get("media_asset_id")) == str(clip.source_asset_id) else 0) + (0.35 if str(screen_focus.get("status")) == "completed" else 0) + min(.35, .12 * len(tags & steam_words))
    landscape_score = (0.4 if AnalysisType.MOOD.value in kinds else 0) + min(.55, .15 * len(tags & landscape_words))
    person_score = .55 if AnalysisType.SPEAKER_STATE.value in kinds else 0

    if steam_score >= max(landscape_score, person_score, .45):
        return WorkspaceContextResponse(timeline_id=timeline.id, clip_id=clip.id, mode="steam", confidence=min(.98, steam_score), reasons=["偵測到螢幕錄影、程式碼或機械／電路分析訊號"], priority_tools=["ar_arrows", "code_highlight", "screen_focus"])
    if landscape_score >= max(person_score, .45):
        return WorkspaceContextResponse(timeline_id=timeline.id, clip_id=clip.id, mode="landscape", confidence=min(.95, landscape_score), reasons=["素材標籤與氛圍分析偏向風景／旅行敘事"], priority_tools=["color_match", "cinematic_transition", "auto_b_roll"])
    if person_score >= .45:
        return WorkspaceContextResponse(timeline_id=timeline.id, clip_id=clip.id, mode="person", confidence=person_score, reasons=["偵測到講者狀態分析"], priority_tools=["portrait_matting", "beauty", "auto_reframe"])
    return WorkspaceContextResponse(timeline_id=timeline.id, clip_id=clip.id, mode="general", confidence=.3, reasons=["尚無足夠的專屬分析訊號，保留通用剪輯工具"], priority_tools=["filter", "captions", "transition"])
