from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.ai.providers.schemas import Transcript
from app.db.session import SessionLocal
from app.core.ai_retry import is_retryable_ai_error, retry_ai_task
from app.core.progress import publish_project_status
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, Timeline
from app.services.education_subtitles import (
    analyze_delivery,
    build_text_overlays,
    extract_education_keywords,
)
from app.worker import celery_app


@celery_app.task(bind=True, name="education.enrich_subtitles")
def enrich_education_subtitles(self, asset_id: str, timeline_id: str) -> dict[str, Any]:
    """Create educational keyword overlays and delivery UX hints for one editable timeline."""
    db = SessionLocal()
    asset: MediaAsset | None = None
    timeline: Timeline | None = None
    try:
        asset = db.get(MediaAsset, UUID(asset_id))
        timeline = db.get(Timeline, UUID(timeline_id))
        if asset is None or timeline is None or timeline.project_id != asset.project_id:
            raise ValueError("Asset and timeline must exist in the same project")

        analysis = db.scalar(
            select(AIAnalysis)
            .where(
                AIAnalysis.media_asset_id == asset.id,
                AIAnalysis.analysis_type == AnalysisType.ROUGH_CUT,
                AIAnalysis.status == "completed",
            )
            .order_by(AIAnalysis.created_at.desc())
        )
        if analysis is None:
            raise ValueError("A completed rough-cut analysis is required")

        transcript = Transcript.model_validate(analysis.result_json.get("transcript", {}))
        publish_project_status(str(asset.project_id), progress=40, stage="education_keywords", message="正在萃取教育關鍵字", job_id=self.request.id)
        delivery_hints = analyze_delivery(transcript, list(analysis.result_json.get("silences", [])))
        transcript.delivery_hints = delivery_hints
        keywords = extract_education_keywords(transcript)
        overlays = build_text_overlays(keywords, transcript)

        settings = dict(timeline.settings_json or {})
        tracks = [track for track in settings.get("effect_tracks", []) if track.get("id") != "education-keywords"]
        tracks.append({"id": "education-keywords", "type": "text_overlay", "items": overlays})
        timeline.settings_json = {**settings, "effect_tracks": tracks}
        analysis.result_json = {
            **analysis.result_json,
            "transcript": transcript.model_dump(mode="json"),
            "education": {
                "keywords": [keyword.model_dump(mode="json") for keyword in keywords],
                "delivery_hints": [hint.model_dump(mode="json") for hint in delivery_hints],
                "text_overlays": overlays,
            },
        }
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="education_completed", status="completed", message="教育字幕增強完成", job_id=self.request.id)
        return {"timeline_id": timeline_id, "overlay_count": len(overlays), "hint_count": len(delivery_hints)}
    except Exception as exc:
        db.rollback()
        if asset is not None and is_retryable_ai_error(exc):
            retry_ai_task(self, exc, project_id=str(asset.project_id), stage="education_keywords", message="教育關鍵字 AI 服務暫時不可用", job_id=self.request.id)
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**current.settings_json, "education": {"status": "failed", "error": str(exc)}}
                db.commit()
        if asset is not None:
            publish_project_status(str(asset.project_id), progress=0, stage="education_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
