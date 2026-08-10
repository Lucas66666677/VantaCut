from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from app.ai.providers.factory import get_asr_provider
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, MediaStatus
from app.services.gaming_highlights import (
    GamingHighlightError,
    build_gaming_highlight_timeline,
    detect_audio_spikes,
    detect_kill_feed_events,
    transcript_reaction_signals,
)
from app.services.storage import download_object
from app.worker import celery_app


def _extract_audio_track(video: Path, output: Path, track_index: int) -> None:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-map", f"0:a:{track_index}", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)],
            check=True, capture_output=True, text=True, timeout=30 * 60,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise GamingHighlightError(f"Unable to extract audio track {track_index}") from exc


@celery_app.task(name="analysis.generate_gaming_highlights")
def generate_gaming_highlights(
    asset_id: str,
    microphone_track_index: int = 0,
    system_track_index: int = 1,
    kill_feed_region: tuple[float, float, float, float] = (0.62, 0.0, 1.0, 0.35),
) -> dict[str, object]:
    db = SessionLocal()
    asset: MediaAsset | None = None
    analysis: AIAnalysis | None = None
    try:
        asset = db.get(MediaAsset, UUID(asset_id))
        if asset is None or asset.status != MediaStatus.READY:
            raise GamingHighlightError("Media asset must be ready before gaming highlight analysis")
        analysis = AIAnalysis(media_asset_id=asset.id, analysis_type=AnalysisType.GAMING_HIGHLIGHTS, model_name="audio-spike+tesseract+asr", status="processing", result_json={})
        db.add(analysis)
        db.commit()
        publish_project_status(str(asset.project_id), progress=5, stage="gaming_highlight_downloading", message="正在下載遊戲錄影素材")

        with tempfile.TemporaryDirectory(prefix=f"gaming-{asset.id}-") as directory:
            workdir = Path(directory)
            video = workdir / "source.mp4"
            microphone_wav = workdir / "microphone.wav"
            system_wav = workdir / "system.wav"
            download_object(asset.storage_key, str(video))
            publish_project_status(str(asset.project_id), progress=20, stage="gaming_audio_tracks", message="正在分離麥克風與遊戲音訊")
            _extract_audio_track(video, microphone_wav, microphone_track_index)
            has_separate_system_track = system_track_index != microphone_track_index
            if has_separate_system_track:
                try:
                    _extract_audio_track(video, system_wav, system_track_index)
                except GamingHighlightError:
                    has_separate_system_track = False

            signals = detect_audio_spikes(microphone_wav, track_name="microphone")
            if has_separate_system_track:
                signals.extend(detect_audio_spikes(system_wav, track_name="system"))
            publish_project_status(str(asset.project_id), progress=45, stage="gaming_asr", message="正在辨識實況主反應")
            transcript = get_asr_provider().transcribe(str(microphone_wav), word_timestamps=True)
            signals.extend(transcript_reaction_signals(transcript))
            publish_project_status(str(asset.project_id), progress=65, stage="gaming_kill_feed_ocr", message="正在辨識擊殺提示")
            signals.extend(detect_kill_feed_events(video, region=kill_feed_region))

        timeline = build_gaming_highlight_timeline(signals, source_asset_id=str(asset.id))
        analysis.status = "completed"
        analysis.confidence = min(1.0, len(timeline["segments"]) / 10) if timeline["segments"] else 0.0
        analysis.result_json = {**timeline, "transcript": transcript.model_dump(mode="json"), "separate_system_track": has_separate_system_track}
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="gaming_highlights_ready", status="completed", message="遊戲高光分析完成")
        return {"analysis_id": str(analysis.id), "asset_id": asset_id, "highlight_count": len(timeline["segments"])}
    except Exception as exc:
        db.rollback()
        if analysis is not None:
            current = db.get(AIAnalysis, analysis.id)
            if current is not None:
                current.status = "failed"
                current.error_message = str(exc)
                db.commit()
        if asset is not None:
            publish_project_status(str(asset.project_id), progress=0, stage="gaming_highlights_failed", status="failed", message=str(exc))
        raise
    finally:
        db.close()
