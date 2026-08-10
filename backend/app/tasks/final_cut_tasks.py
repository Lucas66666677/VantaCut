import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.ai.providers.schemas import Transcript
from app.db.session import SessionLocal
from app.core.ai_retry import is_retryable_ai_error, retry_ai_task
from app.core.progress import publish_project_status
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, Template
from app.services.final_cut import (
    FinalCutError,
    build_candidate_segments,
    extract_segment_frames,
    merge_cut_evidence,
    score_segments_with_provider,
)
from app.services.storage import download_object
from app.worker import celery_app


@celery_app.task(bind=True, name="video.generate_final_cut")
def generate_final_cut(self, asset_id: str, template_id: str | None = None) -> dict[str, Any]:
    """Create a user-reviewable final keep/remove Timeline JSON from all available evidence."""
    db = SessionLocal()
    asset: MediaAsset | None = None
    analysis: AIAnalysis | None = None
    try:
        asset = db.get(MediaAsset, UUID(asset_id))
        if asset is None:
            raise FinalCutError(f"Media asset {asset_id} not found")

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
            raise FinalCutError("Audio rough-cut analysis must complete before final-cut scoring")
        publish_project_status(str(asset.project_id), progress=15, stage="final_cut_preparing", message="正在準備多模態評分", job_id=self.request.id)

        if template_id:
            template = db.get(Template, UUID(template_id))
            if template is None or template.project_id != asset.project_id:
                raise FinalCutError("Template not found in this project")
        else:
            template = db.scalar(
                select(Template)
                .where(Template.project_id == asset.project_id)
                .order_by(Template.created_at.desc())
            )
            if template is None:
                raise FinalCutError("A project template is required for final-cut scoring")

        transcript = Transcript.model_validate(analysis.result_json.get("transcript", {}))
        clip_analysis = list(analysis.result_json.get("clip_analysis", []))
        segments = build_candidate_segments(transcript)
        speaker_analysis = db.scalar(
            select(AIAnalysis)
            .where(
                AIAnalysis.media_asset_id == asset.id,
                AIAnalysis.analysis_type == AnalysisType.SPEAKER_STATE,
                AIAnalysis.status == "completed",
            )
            .order_by(AIAnalysis.created_at.desc())
        )
        speaker_segments = list((speaker_analysis.result_json or {}).get("segments", [])) if speaker_analysis else []

        with tempfile.TemporaryDirectory(prefix=f"final-cut-{asset_id}-") as temp_dir:
            workdir = Path(temp_dir)
            video_path = workdir / "source.mp4"
            download_object(asset.proxy_key or asset.storage_key, str(video_path))
            publish_project_status(str(asset.project_id), progress=35, stage="final_cut_frames", message="正在擷取畫面關鍵影格", job_id=self.request.id)
            frames = extract_segment_frames(video_path, segments, workdir / "frames")
            publish_project_status(str(asset.project_id), progress=60, stage="final_cut_ai_scoring", message="正在進行多模態內容評分", job_id=self.request.id)
            scores = score_segments_with_provider(
                asset.proxy_key or asset.storage_key, template, segments, frames, speaker_segments
            )

        final_segments = merge_cut_evidence(segments, scores, clip_analysis, speaker_segments)
        final_timeline = {
            "version": 1,
            "asset_id": str(asset.id),
            "template_id": str(template.id),
            "segments": final_segments,
        }
        analysis.result_json = {
            **analysis.result_json,
            "multimodal_scores": [score.model_dump(mode="json") for score in scores],
            "final_timeline": final_timeline,
        }
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="final_cut_completed", status="completed", message="最終粗剪建議完成", job_id=self.request.id)
        return {
            "analysis_id": str(analysis.id),
            "asset_id": asset_id,
            "template_id": str(template.id),
            "segment_count": len(final_segments),
        }
    except Exception as exc:
        db.rollback()
        if asset is not None and is_retryable_ai_error(exc):
            retry_ai_task(
                self, exc, project_id=str(asset.project_id), stage="final_cut_ai_scoring",
                message="多模態分析服務暫時不可用", job_id=self.request.id,
            )
        if analysis is not None:
            current = db.get(AIAnalysis, analysis.id)
            if current is not None:
                current.result_json = {**current.result_json, "final_cut_error": str(exc)}
                db.commit()
        if asset is not None:
            publish_project_status(str(asset.project_id), progress=0, stage="final_cut_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
