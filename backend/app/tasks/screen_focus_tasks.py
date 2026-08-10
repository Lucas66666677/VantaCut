from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, Timeline
from app.services.screen_focus import analyze_screen_recording, focus_effects_from_candidates
from app.services.storage import download_object
from app.worker import celery_app


class ScreenFocusTaskError(RuntimeError):
    pass


def _main_segments(document: dict[str, Any]) -> list[dict[str, Any]]:
    for track in document.get("tracks", []):
        if track.get("type") == "main_video":
            return [dict(clip) for clip in track.get("clips", [])]
    return [dict(segment) for segment in document.get("segments", [])]


def _source_time_from_output(segments: list[dict[str, Any]], output_time: float) -> float | None:
    cursor = 0.0
    for segment in segments:
        if segment.get("action", "keep") != "keep":
            continue
        duration = float(segment["source_end"]) - float(segment["source_start"])
        if cursor <= output_time <= cursor + duration:
            return float(segment["source_start"]) + output_time - cursor
        cursor += duration
    return None


def _spoken_cues(timeline: Timeline, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subtitles = dict(timeline.settings_json.get("subtitles", {})).get("items", [])
    cues: list[dict[str, Any]] = []
    for item in subtitles:
        if not isinstance(item, dict):
            continue
        source_time = _source_time_from_output(segments, float(item.get("start_time", 0)))
        if source_time is not None:
            cues.append({"source_time": source_time, "text": str(item.get("text", ""))})
    return cues


@celery_app.task(bind=True, name="screen_focus.analyze_timeline")
def analyze_timeline_screen_focus(
    self, timeline_id: str, use_proxy: bool = True, sample_seconds: float = 0.5,
) -> dict[str, Any]:
    db = SessionLocal()
    timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise ScreenFocusTaskError("Timeline not found")
        confirmed = copy.deepcopy(dict(timeline.settings_json.get("confirmed_timeline", {})))
        source_asset_id = confirmed.get("source_asset_id")
        asset = db.get(MediaAsset, UUID(str(source_asset_id))) if source_asset_id else None
        if asset is None or asset.project_id != timeline.project_id:
            raise ScreenFocusTaskError("Timeline has no valid source asset")
        source_key = asset.proxy_key if use_proxy and asset.proxy_key else asset.storage_key
        segments = _main_segments(confirmed)
        if not segments:
            raise ScreenFocusTaskError("Confirmed timeline has no main-video segments")
        publish_project_status(str(timeline.project_id), progress=10, stage="screen_focus_preparing", message="正在準備螢幕錄影與字幕線索", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"screen-focus-{timeline.id}-") as temporary:
            source = Path(temporary) / "screen-recording.mp4"
            download_object(source_key, str(source))
            publish_project_status(str(timeline.project_id), progress=35, stage="screen_focus_tracking", message="正在追蹤游標、活躍視窗與畫面文字", job_id=self.request.id)
            report = analyze_screen_recording(source, spoken_cues=_spoken_cues(timeline, segments), sample_seconds=sample_seconds)
        effects = focus_effects_from_candidates(report, segments)
        report["effects"] = effects
        report["source"] = "proxy" if source_key == asset.proxy_key else "original"
        report["status"] = "completed"
        confirmed["screen_focus_effects"] = effects
        settings = {**dict(timeline.settings_json or {}), "confirmed_timeline": confirmed, "multitrack_timeline": confirmed, "screen_focus": report}
        timeline.settings_json = settings
        analysis = AIAnalysis(media_asset_id=asset.id, analysis_type=AnalysisType.SCREEN_FOCUS, model_name="opencv+tesseract-screen-focus-v1", status="completed", result_json=report, confidence=(sum(float(item["confidence"]) for item in effects) / len(effects) if effects else None))
        db.add(analysis)
        db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="screen_focus_completed", status="completed", message=f"已建立 {len(effects)} 個教學聚焦效果", job_id=self.request.id)
        return {"timeline_id": timeline_id, "effects": effects, "focus_candidate_count": len(report.get("focus_candidates", []))}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "screen_focus": {"status": "failed", "error": str(exc)}}
                db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="screen_focus_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
