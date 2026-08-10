from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, MediaStatus, Timeline
from app.services.behavioral_coach import analyze_vocal_stability, apply_coach_markers_to_timeline, build_behavioral_coach_report
from app.services.speaker_state import SpeakerSegment, analyze_speaker_delivery
from app.services.storage import download_object
from app.worker import celery_app


def _transcript(rough_cut: AIAnalysis) -> list[dict[str, Any]]:
    return [dict(item) for item in dict((rough_cut.result_json or {}).get("transcript", {})).get("segments", []) if float(item.get("end", 0)) > float(item.get("start", 0))]


@celery_app.task(bind=True, name="analysis.analyze_behavioral_coach")
def analyze_behavioral_coach(self, media_asset_id: str, timeline_id: str | None = None) -> dict[str, Any]:
    db = SessionLocal(); asset = None; analysis = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None or asset.status != MediaStatus.READY:
            raise ValueError("A ready media asset is required")
        rough_cut = db.scalar(select(AIAnalysis).where(
            AIAnalysis.media_asset_id == asset.id, AIAnalysis.analysis_type == AnalysisType.ROUGH_CUT,
            AIAnalysis.status == "completed",
        ).order_by(AIAnalysis.created_at.desc()))
        if rough_cut is None:
            raise ValueError("Timed ASR / rough-cut analysis is required before behavioral coaching")
        transcript = _transcript(rough_cut)
        if not transcript:
            raise ValueError("Timed transcript has no usable segments")
        timeline = db.get(Timeline, UUID(timeline_id)) if timeline_id else None
        if timeline and timeline.project_id != asset.project_id:
            raise ValueError("Timeline must belong to the media project")
        analysis = AIAnalysis(media_asset_id=asset.id, analysis_type=AnalysisType.SPEAKER_STATE, model_name="behavioral_coach_v1", status="processing", result_json={})
        db.add(analysis); db.commit()
        publish_project_status(str(asset.project_id), progress=10, stage="behavioral_coach_preparing", message="正在準備演講呈現教練分析", job_id=self.request.id)
        segments = [SpeakerSegment(id=f"segment-{index:04d}", source_start=float(item["start"]), source_end=float(item["end"])) for index, item in enumerate(transcript, start=1)]
        with tempfile.TemporaryDirectory(prefix=f"behavioral-coach-{asset.id}-") as temporary:
            workdir = Path(temporary); video, audio = workdir / "proxy.mp4", workdir / "audio.wav"
            download_object(asset.proxy_key or asset.storage_key, str(video))
            if asset.audio_key:
                download_object(asset.audio_key, str(audio))
            else:
                subprocess.run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ar", "16000", "-ac", "1", str(audio)], check=True, capture_output=True, timeout=300)
            publish_project_status(str(asset.project_id), progress=35, stage="behavioral_coach_visual", message="正在追蹤眼神、姿勢、手勢與可用 FACS 動作單元", job_id=self.request.id)
            visual = analyze_speaker_delivery(video, segments)
            publish_project_status(str(asset.project_id), progress=65, stage="behavioral_coach_voice", message="正在分析語速、音高輪廓與穩定度", job_id=self.request.id)
            vocal = analyze_vocal_stability(str(audio), transcript)
        report = build_behavioral_coach_report(visual_segments=visual, transcript_segments=transcript, vocal=vocal)
        analysis.status, analysis.confidence, analysis.result_json = "completed", 0.8, report
        if timeline:
            apply_coach_markers_to_timeline(timeline, report)
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="behavioral_coach_completed", status="completed", message="行為分析與教練報告已完成", job_id=self.request.id)
        return {"analysis_id": str(analysis.id), "media_asset_id": media_asset_id, "marked_segments": len(report["lowest_confidence_segments"])}
    except Exception as exc:
        db.rollback()
        if analysis:
            current = db.get(AIAnalysis, analysis.id)
            if current: current.status, current.error_message = "failed", str(exc); db.commit()
        if asset: publish_project_status(str(asset.project_id), progress=0, stage="behavioral_coach_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
