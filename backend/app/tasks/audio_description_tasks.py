"""Background generation of concise audio descriptions for dialogue-free final-cut intervals."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.ai.providers.factory import get_vision_provider
from app.core.ai_retry import is_retryable_ai_error, retry_ai_task
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, Timeline
from app.services.audio_description import (
    AUDIO_DESCRIPTION_SYSTEM_PROMPT, AudioDescriptionError, build_audio_description_track, build_description_prompt,
    description_limits, extract_visual_excerpt, fit_description_to_gap, main_keep_segments, synthesize_description,
    transcript_gaps, validate_description,
)
from app.services.storage import download_object, upload_object
from app.worker import celery_app


DESCRIPTION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["description", "visual_focus", "word_count"],
    "properties": {"description": {"type": "string"}, "visual_focus": {"type": "string"}, "word_count": {"type": "integer", "minimum": 1}},
}


def _latest_transcript(db, asset_id: UUID) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    analyses = db.query(AIAnalysis).filter(AIAnalysis.media_asset_id == asset_id, AIAnalysis.status == "completed").order_by(AIAnalysis.created_at.desc()).all()
    for analysis in analyses:
        result = dict(analysis.result_json or {})
        transcript = result.get("transcript")
        if isinstance(transcript, dict) and isinstance(transcript.get("segments"), list):
            return transcript, list(result.get("silences", []))
    raise AudioDescriptionError("Run audio rough-cut analysis first so dialogue gaps can be safely detected")


@celery_app.task(bind=True, name="accessibility.generate_audio_description")
def generate_audio_description(self, timeline_id: str) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise AudioDescriptionError("Timeline not found")
        settings_json = dict(timeline.settings_json or {}); request = dict(settings_json.get("audio_description", {})); confirmed = dict(settings_json.get("confirmed_timeline", {}))
        asset = db.get(MediaAsset, UUID(str(confirmed.get("source_asset_id", "")))) if confirmed.get("source_asset_id") else None
        if asset is None or asset.project_id != timeline.project_id:
            raise AudioDescriptionError("Confirmed Timeline source asset is invalid")
        source_key = asset.proxy_key or asset.storage_key
        if not source_key:
            raise AudioDescriptionError("Source asset has no proxy/original video")
        keep_segments = main_keep_segments(confirmed)
        if not keep_segments:
            raise AudioDescriptionError("Confirmed Timeline has no retained main-video ranges")
        transcript, silences = _latest_transcript(db, asset.id)
        source_duration = float(asset.duration_seconds or max(item["source_end"] for item in keep_segments))
        language, min_gap = str(request.get("language", "zh")), float(request.get("min_gap_seconds", 2.0))
        gaps = transcript_gaps(transcript, keep_segments, min_gap_seconds=min_gap, source_duration=source_duration, silences=silences)
        if not gaps:
            raise AudioDescriptionError("No dialogue-free gaps long enough for accessible narration were found")
        final_duration = keep_segments[-1]["output_end"]
        publish_project_status(str(timeline.project_id), progress=8, stage="audio_description_planning", message="正在找出可放入口述影像的對白空檔", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"audio-description-{timeline.id}-") as temporary:
            workdir = Path(temporary); source_video = workdir / "source.mp4"; download_object(source_key, str(source_video))
            cues: list[dict[str, Any]] = []
            provider = get_vision_provider()
            for index, gap in enumerate(gaps):
                limits = description_limits(gap.duration, language)
                excerpt = workdir / f"gap-{index}.mp4"; extract_visual_excerpt(source_video, excerpt, start=gap.source_start, end=gap.source_end)
                payload = provider.analyze_video(
                    str(excerpt),
                    f"{AUDIO_DESCRIPTION_SYSTEM_PROMPT}\n\n{build_description_prompt(gap=gap, language=language, limits=limits)}",
                    response_schema=DESCRIPTION_SCHEMA,
                    context={"task": "audio_description", "source_start": gap.source_start, "source_end": gap.source_end, "max_words": limits["max_words"], "language": language},
                )
                text = validate_description(payload, limits=limits, language=language)
                raw, fitted = workdir / f"description-{index}-raw.wav", workdir / f"description-{index}.wav"
                synthesize_description(text=text, language=language, output_wav=raw)
                fit_description_to_gap(raw, fitted, duration_seconds=gap.duration)
                cues.append({"id": str(uuid4()), "source_start": round(gap.source_start, 3), "source_end": round(gap.source_end, 3), "output_start": round(gap.output_start, 3), "output_end": round(gap.output_end, 3), "audio_context": gap.audio_context, "text": text, "max_words": limits["max_words"], "local_path": str(fitted)})
                publish_project_status(str(timeline.project_id), progress=15 + int((index + 1) / len(gaps) * 72), stage="audio_description_writing", message="正在生成精簡口述影像", job_id=self.request.id)
            track = workdir / "audio-description.wav"; build_audio_description_track(cues, output_duration=final_duration, output_wav=track)
            key = f"projects/{timeline.project_id}/timelines/{timeline.id}/accessibility/audio-description.wav"; upload_object(key, str(track), "audio/wav")
        public_cues = [{key: value for key, value in cue.items() if key != "local_path"} for cue in cues]
        timeline.settings_json = {**dict(timeline.settings_json or {}), "audio_description": {"status": "completed", "language": language, "min_gap_seconds": min_gap, "audio_key": key, "cues": public_cues, "output_duration": final_duration, "render_mode": "selectable_track_with_ducking", "ducking": {"ratio": 8, "attack_ms": 15, "release_ms": 280}}}; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="audio_description_completed", status="completed", message="口述影像音軌已建立，可在導出時選取", job_id=self.request.id)
        return {"timeline_id": timeline_id, "audio_key": key, "cue_count": len(public_cues), "status": "completed"}
    except Exception as exc:
        db.rollback()
        if timeline is not None and is_retryable_ai_error(exc):
            retry_ai_task(self, exc, project_id=str(timeline.project_id), stage="audio_description_writing", message="口述影像模型暫時不可用", job_id=self.request.id)
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "audio_description": {"status": "failed", "error": str(exc)}}; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="audio_description_failed", status="failed", message="口述影像生成失敗，請重試", job_id=self.request.id)
        raise
    finally:
        db.close()
