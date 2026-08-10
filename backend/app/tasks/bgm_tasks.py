import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal
from app.core.ai_retry import is_retryable_ai_error, retry_ai_task
from app.core.progress import publish_project_status
from app.models.entities import MediaAsset, Timeline
from app.services.bgm_recommender import kept_segments_from_timeline, recommend_bgm
from app.services.storage import download_object
from app.worker import celery_app


@celery_app.task(bind=True, name="bgm.recommend_for_timeline")
def recommend_bgm_for_timeline(self, timeline_id: str) -> dict[str, Any]:
    """Persist BGM search keywords for the final confirmed timeline; no music is licensed or supplied."""
    db = SessionLocal()
    timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise ValueError("Timeline not found")
        confirmed = dict(timeline.settings_json.get("confirmed_timeline", {}))
        asset_id = confirmed.get("source_asset_id")
        if not asset_id:
            raise ValueError("Timeline has no confirmed source asset")
        asset = db.get(MediaAsset, UUID(asset_id))
        if asset is None or asset.project_id != timeline.project_id:
            raise ValueError("Confirmed source asset is invalid")

        kept_segments = kept_segments_from_timeline(list(confirmed.get("segments", [])))
        publish_project_status(str(timeline.project_id), progress=20, stage="bgm_preparing", message="正在準備氛圍分析", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"bgm-{timeline_id}-") as temp_dir:
            workdir = Path(temp_dir)
            video_path = workdir / "timeline-source.mp4"
            video_key = asset.proxy_key or asset.storage_key
            download_object(video_key, str(video_path))
            publish_project_status(str(timeline.project_id), progress=45, stage="bgm_frame_sampling", message="正在抽取畫面關鍵影格", job_id=self.request.id)
            recommendation = recommend_bgm(video_key, kept_segments, video_path, workdir)

        timeline.settings_json = {
            **timeline.settings_json,
            "bgm_recommendation": recommendation.model_dump(mode="json"),
        }
        db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="bgm_completed", status="completed", message="BGM 搜尋建議完成", job_id=self.request.id)
        return {"timeline_id": timeline_id, **recommendation.model_dump(mode="json")}
    except Exception as exc:
        db.rollback()
        if timeline is not None and is_retryable_ai_error(exc):
            retry_ai_task(self, exc, project_id=str(timeline.project_id), stage="bgm_analysis", message="BGM AI 分析服務暫時不可用", job_id=self.request.id)
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**current.settings_json, "bgm_recommendation": {"status": "failed", "error": str(exc)}}
                db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="bgm_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
