"""Background tasks for explainable speaker delivery scoring and opt-in gaze correction."""
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
from app.services.gaze_redirection import redirect_gaze_video
from app.services.speaker_state import SpeakerSegment, analyze_speaker_delivery
from app.services.talking_head_confidence import apply_talking_head_markers, derive_talking_head_markers
from app.services.storage import download_object, upload_object
from app.worker import celery_app


class SpeakerTaskError(RuntimeError):
    pass


def _transcript_segments(rough_cut: AIAnalysis) -> list[SpeakerSegment]:
    transcript = dict((rough_cut.result_json or {}).get("transcript", {}))
    segments = list(transcript.get("segments", []))
    result = [
        SpeakerSegment(
            id=f"segment-{index:04d}",
            source_start=float(item["start"]),
            source_end=float(item["end"]),
        )
        for index, item in enumerate(segments, start=1)
        if float(item.get("end", 0)) > float(item.get("start", 0))
    ]
    if result:
        return result
    duration = float(rough_cut.media_asset.duration_seconds or 0)
    return [SpeakerSegment(id="segment-0001", source_start=0.0, source_end=duration)] if duration > 0 else []


@celery_app.task(bind=True, name="analysis.analyze_speaker_state")
def analyze_speaker_state(
    self, media_asset_id: str, timeline_id: str | None = None, confidence_threshold: int = 58,
    enable_gaze_correction: bool = False, use_proxy_for_gaze: bool = True,
) -> dict[str, Any]:
    """Score delivery from landmarks/pose data; results are advisory, never automatic cuts."""
    db = SessionLocal()
    asset: MediaAsset | None = None
    analysis: AIAnalysis | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None or asset.status != MediaStatus.READY:
            raise SpeakerTaskError("A ready media asset is required for speaker-state analysis")
        rough_cut = db.scalar(
            select(AIAnalysis)
            .where(
                AIAnalysis.media_asset_id == asset.id,
                AIAnalysis.analysis_type == AnalysisType.ROUGH_CUT,
                AIAnalysis.status == "completed",
            )
            .order_by(AIAnalysis.created_at.desc())
        )
        if rough_cut is None:
            raise SpeakerTaskError("Audio rough-cut analysis must complete before speaker-state analysis")
        segments = _transcript_segments(rough_cut)
        if not segments:
            raise SpeakerTaskError("No timed transcript segments are available")

        analysis = AIAnalysis(
            media_asset_id=asset.id,
            analysis_type=AnalysisType.SPEAKER_STATE,
            model_name="mediapipe_face_mesh_pose",
            status="processing",
            result_json={},
        )
        db.add(analysis)
        db.commit()
        publish_project_status(str(asset.project_id), progress=8, stage="speaker_state_downloading", message="正在準備講者畫面分析", job_id=self.request.id)

        with tempfile.TemporaryDirectory(prefix=f"speaker-state-{asset.id}-") as temporary:
            source = Path(temporary) / "source.mp4"
            download_object(asset.proxy_key or asset.storage_key, str(source))
            publish_project_status(str(asset.project_id), progress=35, stage="speaker_state_tracking", message="正在分析眼神、姿勢與手勢", job_id=self.request.id)
            results = analyze_speaker_delivery(source, segments)

        analysis.status = "completed"
        analysis.confidence = 1.0
        analysis.result_json = {
            "version": 1,
            "advisory_only": True,
            "segments": results,
            "limitations": [
                "分數描述鏡頭內的呈現品質，不用於身份、生理或人格判定。",
                "未偵測到臉部或姿勢時不會推論負面表現；創作者應自行確認建議。",
            ],
        }
        markers: list[dict[str, Any]] = []
        if timeline_id:
            timeline = db.get(Timeline, UUID(timeline_id))
            if timeline is None or timeline.project_id != asset.project_id:
                raise SpeakerTaskError("Timeline does not belong to the analyzed media project")
            markers = derive_talking_head_markers(results, dict(rough_cut.result_json or {}), confidence_threshold=confidence_threshold)
            apply_talking_head_markers(timeline, markers)
            talking_head = dict((timeline.settings_json or {}).get("talking_head_confidence", {}))
            timeline.settings_json = {**dict(timeline.settings_json or {}), "talking_head_confidence": {**talking_head, "status": "completed", "gaze_correction": {"status": "queued", "source_asset_id": str(asset.id)} if enable_gaze_correction else None}}
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="speaker_state_completed", status="completed", message="講者狀態建議完成", job_id=self.request.id)
        if timeline_id and enable_gaze_correction:
            redirect_gaze.delay(str(asset.id), use_proxy_for_gaze, timeline_id)
        return {"analysis_id": str(analysis.id), "media_asset_id": media_asset_id, "segment_count": len(results), "marker_count": len(markers)}
    except Exception as exc:
        db.rollback()
        if analysis is not None:
            current = db.get(AIAnalysis, analysis.id)
            if current is not None:
                current.status = "failed"
                current.error_message = str(exc)
                db.commit()
        if asset is not None:
            publish_project_status(str(asset.project_id), progress=0, stage="speaker_state_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="video.redirect_gaze")
def redirect_gaze(self, media_asset_id: str, use_proxy: bool = True, timeline_id: str | None = None) -> dict[str, Any]:
    """Apply a provisioned gaze-GAN only after explicit API consent, preserving source audio."""
    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None or asset.status != MediaStatus.READY:
            raise SpeakerTaskError("A ready media asset is required for gaze redirection")
        source_key = asset.proxy_key if use_proxy and asset.proxy_key else asset.storage_key
        publish_project_status(str(asset.project_id), progress=5, stage="gaze_redirect_downloading", message="正在準備眼神修正素材", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"gaze-redirect-{asset.id}-") as temporary:
            workdir = Path(temporary)
            source, silent, output = workdir / "source.mp4", workdir / "gaze-silent.mp4", workdir / "gaze-corrected.mp4"
            download_object(source_key, str(source))
            publish_project_status(str(asset.project_id), progress=20, stage="gaze_redirect_processing", message="正在進行逐幀眼神修正", job_id=self.request.id)
            redirect_gaze_video(source, silent)
            publish_project_status(str(asset.project_id), progress=88, stage="gaze_redirect_muxing", message="正在同步原始音訊", job_id=self.request.id)
            try:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", str(silent), "-i", str(source), "-map", "0:v:0", "-map", "1:a?",
                        "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(output),
                    ], check=True, capture_output=True, text=True, timeout=2 * 60 * 60,
                )
            except subprocess.TimeoutExpired as exc:
                raise SpeakerTaskError("Gaze redirection muxing timed out") from exc
            except subprocess.CalledProcessError as exc:
                raise SpeakerTaskError((exc.stderr or "Gaze redirection muxing failed")[-2000:]) from exc
            output_key = f"projects/{asset.project_id}/derived/{asset.id}/gaze-redirected.mp4"
            upload_object(output_key, str(output), "video/mp4")

        asset.metadata_json = {
            **dict(asset.metadata_json or {}),
            "gaze_redirection": {
                "status": "completed", "engine": "onnx_gan", "output_key": output_key,
                "source": "proxy" if source_key == asset.proxy_key else "original", "explicit_consent": True,
            },
        }
        if timeline_id:
            timeline = db.get(Timeline, UUID(timeline_id))
            if timeline is None or timeline.project_id != asset.project_id:
                raise SpeakerTaskError("Timeline does not belong to gaze source media")
            settings = dict(timeline.settings_json or {}); talking_head = dict(settings.get("talking_head_confidence", {}))
            settings["talking_head_confidence"] = {**talking_head, "status": "completed", "gaze_correction": {"status": "completed", "source_asset_id": str(asset.id), "output_key": output_key, "source": "proxy" if source_key == asset.proxy_key else "original", "explicit_consent": True}}
            timeline.settings_json = settings
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="gaze_redirect_completed", status="completed", message="眼神修正預覽完成", job_id=self.request.id)
        return {"media_asset_id": media_asset_id, "output_key": output_key}
    except Exception as exc:
        db.rollback()
        if asset is not None:
            current = db.get(MediaAsset, asset.id)
            if current is not None:
                current.metadata_json = {
                    **dict(current.metadata_json or {}),
                    "gaze_redirection": {"status": "failed", "error": str(exc), "explicit_consent": True},
                }
                db.commit()
            if timeline_id:
                timeline = db.get(Timeline, UUID(timeline_id))
                if timeline is not None and timeline.project_id == asset.project_id:
                    settings = dict(timeline.settings_json or {}); talking_head = dict(settings.get("talking_head_confidence", {}))
                    settings["talking_head_confidence"] = {**talking_head, "status": "completed", "gaze_correction": {"status": "failed", "source_asset_id": str(asset.id), "error": str(exc), "explicit_consent": True}}
                    timeline.settings_json = settings; db.commit()
            publish_project_status(str(asset.project_id), progress=0, stage="gaze_redirect_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
