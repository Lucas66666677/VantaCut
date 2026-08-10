"""ASR + LLM language teaching review for timestamped video overlays."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.ai.providers.schemas import Transcript
from app.core.ai_retry import is_retryable_ai_error, retry_ai_task
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, Timeline
from app.services.language_review import LanguageReviewError, run_language_review
from app.worker import celery_app


@celery_app.task(bind=True, name="education.review_language_video")
def review_language_video(self, asset_id: str, timeline_id: str, target: str = "ielts_speaking") -> dict[str, Any]:
    db = SessionLocal(); asset: MediaAsset | None = None; timeline: Timeline | None = None
    try:
        asset, timeline = db.get(MediaAsset, UUID(asset_id)), db.get(Timeline, UUID(timeline_id))
        if asset is None or timeline is None or asset.project_id != timeline.project_id:
            raise LanguageReviewError("Media asset and Timeline must belong to the same project")
        analysis = db.scalar(select(AIAnalysis).where(AIAnalysis.media_asset_id == asset.id, AIAnalysis.analysis_type == AnalysisType.ROUGH_CUT, AIAnalysis.status == "completed").order_by(AIAnalysis.created_at.desc()))
        if analysis is None:
            raise LanguageReviewError("Run rough-cut audio analysis first to obtain word-level ASR timestamps")
        transcript = Transcript.model_validate(dict(analysis.result_json or {}).get("transcript", {}))
        confirmed = dict(timeline.settings_json.get("confirmed_timeline", {}))
        if str(confirmed.get("source_asset_id", "")) != str(asset.id):
            raise LanguageReviewError("Language review requires the confirmed Timeline for this source asset")
        publish_project_status(str(asset.project_id), progress=20, stage="language_review_asr", message="正在讀取具時間戳的英語逐字稿", job_id=self.request.id)
        review, overlays = run_language_review(transcript=transcript, silences=list(dict(analysis.result_json or {}).get("silences", [])), confirmed_timeline=confirmed, target=target)
        publish_project_status(str(asset.project_id), progress=82, stage="language_review_overlays", message="正在生成文法修正與同義詞教學圖層", job_id=self.request.id)
        settings = dict(timeline.settings_json or {})
        tracks = [track for track in settings.get("effect_tracks", []) if track.get("id") != "language-teaching-review"]
        tracks.append({"id": "language-teaching-review", "type": "text_overlay", "z_index": 80, "items": overlays})
        timeline.settings_json = {**settings, "effect_tracks": tracks, "language_review": {"status": "completed", "target": target, "review": review, "overlays": overlays}}
        analysis.result_json = {**dict(analysis.result_json or {}), "language_review": review}
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="language_review_completed", status="completed", message="語言教學審閱與動態糾錯圖層已生成", job_id=self.request.id)
        return {"timeline_id": timeline_id, "issue_count": len(review["issues"]), "overlay_count": len(overlays), "scores": review["scores"]}
    except Exception as exc:
        db.rollback()
        if asset is not None and is_retryable_ai_error(exc):
            retry_ai_task(self, exc, project_id=str(asset.project_id), stage="language_review_llm", message="語言分析模型暫時不可用", job_id=self.request.id)
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "language_review": {"status": "failed", "error": str(exc)}}; db.commit()
        if asset is not None:
            publish_project_status(str(asset.project_id), progress=0, stage="language_review_failed", status="failed", message="語言教學審閱失敗，請重試", job_id=self.request.id)
        raise
    finally:
        db.close()
