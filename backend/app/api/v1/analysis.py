from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import AIFeedback, Clip, MediaAsset, Timeline, User
from app.schemas.feedback import AIFeedbackCreate, AIFeedbackResponse
from app.schemas.template import ExtractTemplateRequest, TemplateResponse
from app.schemas.gaming import GamingHighlightQueuedResponse, GamingHighlightRequest
from app.schemas.retention import RetentionPredictionRequest, RetentionPredictionResponse
from app.schemas.hook import HookCheckRequest, HookReport, HookRescueRequest, HookRescueResponse
from app.services.hook_detector import analyze_opening_hook, apply_hook_rescue
from app.services.retention_prediction import predict_timeline_retention
from app.tasks.gaming_highlight_tasks import generate_gaming_highlights
from app.services.template_extraction import TemplateExtractionError, extract_template
from app.schemas.language_review import LanguageReviewQueuedResponse, LanguageReviewRequest
from app.tasks.language_review_tasks import review_language_video

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/timelines/{timeline_id}/hook-check", response_model=HookReport)
def check_opening_hook(timeline_id: str, payload: HookCheckRequest, db: Session = Depends(get_db)) -> HookReport:
    from uuid import UUID

    try:
        timeline = db.get(Timeline, UUID(timeline_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid timeline ID") from exc
    user = db.get(User, payload.user_id)
    if timeline is None or user is None:
        raise HTTPException(status_code=404, detail="Timeline or user not found")
    if timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot inspect this timeline")
    try:
        report = analyze_opening_hook(db, timeline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    timeline.settings_json = {**dict(timeline.settings_json or {}), "hook_report": {"status": "completed", **report}}
    db.commit()
    return HookReport(timeline_id=timeline.id, **report)


@router.post("/timelines/{timeline_id}/hook-rescue", response_model=HookRescueResponse, status_code=status.HTTP_201_CREATED)
def rescue_opening_hook(timeline_id: str, payload: HookRescueRequest, db: Session = Depends(get_db)) -> HookRescueResponse:
    from uuid import UUID

    try:
        timeline = db.get(Timeline, UUID(timeline_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid timeline ID") from exc
    user = db.get(User, payload.user_id)
    if timeline is None or user is None:
        raise HTTPException(status_code=404, detail="Timeline or user not found")
    if timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    try:
        rescued = apply_hook_rescue(db, timeline)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    highlight = dict((rescued.settings_json or {}).get("hook_rescue") or {}).get("highlight", {})
    return HookRescueResponse(source_timeline_id=timeline.id, timeline_id=rescued.id, status="applied", inserted_duration_seconds=float(highlight.get("duration", 0)), message="已建立可復原的黃金 Hook 救援版本：最佳片段、黑白轉彩色與 Boom 已加入開場。")


@router.post("/language-review", response_model=LanguageReviewQueuedResponse, status_code=status.HTTP_202_ACCEPTED)
def request_language_review(payload: LanguageReviewRequest, db: Session = Depends(get_db)) -> LanguageReviewQueuedResponse:
    asset, timeline, user = db.get(MediaAsset, payload.media_asset_id), db.get(Timeline, payload.timeline_id), db.get(User, payload.user_id)
    if asset is None or timeline is None or user is None:
        raise HTTPException(status_code=404, detail="Media asset, Timeline, or user not found")
    if asset.project_id != timeline.project_id or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot review this media/timeline")
    timeline.settings_json = {**dict(timeline.settings_json or {}), "language_review": {"status": "queued", "target": payload.target, "language": payload.language}}
    db.commit(); task = review_language_video.delay(str(asset.id), str(timeline.id), payload.target)
    return LanguageReviewQueuedResponse(task_id=task.id, timeline_id=timeline.id, status="queued")


@router.post("/timelines/{timeline_id}/retention-prediction", response_model=RetentionPredictionResponse)
def predict_retention_before_export(
    timeline_id: str,
    payload: RetentionPredictionRequest,
    db: Session = Depends(get_db),
) -> RetentionPredictionResponse:
    """Estimate risk before render; never represent this as observed YouTube/TikTok analytics."""
    from uuid import UUID

    try:
        timeline = db.get(Timeline, UUID(timeline_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid timeline ID") from exc
    user = db.get(User, payload.user_id)
    if timeline is None or user is None:
        raise HTTPException(status_code=404, detail="Timeline or user not found")
    if timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot predict retention for this project")
    try:
        prediction = predict_timeline_retention(db, timeline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    timeline.settings_json = {
        **dict(timeline.settings_json or {}),
        "retention_prediction": {"status": "completed", **prediction},
    }
    db.commit()
    return RetentionPredictionResponse(timeline_id=timeline.id, **prediction)


@router.post("/gaming-highlights", response_model=GamingHighlightQueuedResponse, status_code=status.HTTP_202_ACCEPTED)
def request_gaming_highlights(payload: GamingHighlightRequest) -> GamingHighlightQueuedResponse:
    task = generate_gaming_highlights.delay(
        str(payload.media_asset_id),
        payload.microphone_track_index,
        payload.system_track_index,
        payload.kill_feed_region,
    )
    return GamingHighlightQueuedResponse(task_id=task.id, media_asset_id=payload.media_asset_id, status="queued")


@router.post("/feedback", response_model=AIFeedbackResponse, status_code=status.HTTP_201_CREATED)
def record_analysis_feedback(
    payload: AIFeedbackCreate,
    db: Session = Depends(get_db),
) -> AIFeedbackResponse:
    """Record a user override without mutating the original AI result."""
    timeline = db.get(Timeline, payload.timeline_id)
    user = db.get(User, payload.user_id)
    if timeline is None or user is None:
        raise HTTPException(status_code=404, detail="Timeline or user not found")
    if timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot submit feedback for this project")

    clip = db.get(Clip, payload.clip_id) if payload.clip_id else None
    if payload.clip_id is not None and (clip is None or clip.timeline_id != timeline.id):
        raise HTTPException(status_code=404, detail="Clip not found in this timeline")

    feedback = AIFeedback(
        user_id=user.id,
        project_id=timeline.project_id,
        timeline_id=timeline.id,
        clip_id=clip.id if clip else None,
        original_ai_decision=payload.original_ai_decision,
        user_final_decision=payload.user_final_decision,
        clip_context_features=payload.clip_context_features,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return AIFeedbackResponse(
        id=feedback.id,
        project_id=feedback.project_id,
        timeline_id=feedback.timeline_id,
        clip_id=feedback.clip_id,
        original_ai_decision=feedback.original_ai_decision,  # type: ignore[arg-type]
        user_final_decision=feedback.user_final_decision,  # type: ignore[arg-type]
        clip_context_features=feedback.clip_context_features,
        created_at=feedback.created_at,
    )


@router.post(
    "/extract-template",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def extract_template_endpoint(
    payload: ExtractTemplateRequest,
    db: Session = Depends(get_db),
) -> TemplateResponse:
    try:
        template = extract_template(db, payload.media_asset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TemplateExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return TemplateResponse(
        id=template.id,
        project_id=template.project_id,
        source_asset_id=template.source_asset_id,
        name=template.name,
        structure=template.structure_json,
    )
