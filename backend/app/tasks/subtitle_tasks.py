import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.ai.providers.factory import get_asr_provider
from app.core.ai_retry import is_retryable_ai_error, retry_ai_task
from app.core.config import settings
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, Timeline
from app.schemas.subtitle import ConfirmedTimelineSegment, SubtitleCue
from app.services.storage import download_object, upload_bytes, upload_object
from app.services.subtitles import cues_to_ass, cues_to_srt, transcript_to_cues
from app.services.bilingual_subtitles import (
    bilingual_to_ass,
    bilingual_to_srt,
    bilingual_to_vtt,
    source_track_to_vtt,
    target_track_to_srt,
    target_track_to_vtt,
    translate_cues_contextually,
)
from app.services.kinetic_subtitles import annotate_transcript_kinetics
from app.services.kinetic_overlay import render_kinetic_webm
from app.schemas.academic import AcademicGlossaryEntry
from app.services.academic import apply_glossary_to_cues
from app.worker import celery_app


FFMPEG_TIMEOUT_SECONDS = 10 * 60


class SubtitleGenerationError(RuntimeError):
    pass


def _create_bilingual_artifacts(
    *, timeline: Timeline, cues: list[SubtitleCue], source_language: str | None,
    target_language: str, caption_style: dict[str, Any], glossary: list[dict[str, Any]],
) -> dict[str, Any]:
    translated = translate_cues_contextually(
        cues, source_language=source_language, target_language=target_language, glossary=glossary,
    )
    preset = str(caption_style.get("preset", "viral_yellow"))
    aspect_ratio = str(caption_style.get("aspect_ratio", "9:16"))
    base_key = f"projects/{timeline.project_id}/timelines/{timeline.id}/subtitles/bilingual/{target_language}"
    keys = {
        "bilingual_srt_key": f"{base_key}/bilingual.srt",
        "bilingual_vtt_key": f"{base_key}/bilingual.vtt",
        "source_srt_key": f"{base_key}/source.srt",
        "source_vtt_key": f"{base_key}/source.vtt",
        "target_srt_key": f"{base_key}/target.srt",
        "target_vtt_key": f"{base_key}/target.vtt",
        "ass_key": f"{base_key}/bilingual.ass",
    }
    upload_bytes(keys["bilingual_srt_key"], bilingual_to_srt(translated).encode("utf-8"), "application/x-subrip")
    upload_bytes(keys["bilingual_vtt_key"], bilingual_to_vtt(translated).encode("utf-8"), "text/vtt")
    upload_bytes(keys["source_srt_key"], cues_to_srt(cues).encode("utf-8"), "application/x-subrip")
    upload_bytes(keys["source_vtt_key"], source_track_to_vtt(cues).encode("utf-8"), "text/vtt")
    upload_bytes(keys["target_srt_key"], target_track_to_srt(translated).encode("utf-8"), "application/x-subrip")
    upload_bytes(keys["target_vtt_key"], target_track_to_vtt(translated).encode("utf-8"), "text/vtt")
    upload_bytes(keys["ass_key"], bilingual_to_ass(cues, translated, preset=preset, aspect_ratio=aspect_ratio).encode("utf-8"), "text/x-ssa")
    return {"status": "completed", "source_language": source_language, "target_language": target_language, "items": translated, "caption_preset": preset, "caption_aspect_ratio": aspect_ratio, **keys}


def _trim_audio(source: Path, target: Path, start: float, end: float) -> None:
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(source), "-ss", f"{start:.3f}",
                "-t", f"{end - start:.3f}", "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SubtitleGenerationError("Audio trim timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise SubtitleGenerationError(f"Audio trim failed: {(exc.stderr or '')[-1000:]}") from exc


@celery_app.task(bind=True, name="subtitle.generate_subtitles_for_timeline")
def generate_subtitles_for_timeline(self, timeline_id: str) -> dict[str, Any]:
    """Generate timeline-relative subtitle cues and SRT/ASS artifacts for confirmed keep ranges."""
    db = SessionLocal()
    timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise SubtitleGenerationError(f"Timeline {timeline_id} not found")
        confirmed = dict(timeline.settings_json.get("confirmed_timeline", {}))
        source_asset_id = confirmed.get("source_asset_id")
        if not source_asset_id:
            raise SubtitleGenerationError("Confirmed timeline has no source_asset_id")
        asset = db.get(MediaAsset, UUID(source_asset_id))
        if asset is None or asset.project_id != timeline.project_id:
            raise SubtitleGenerationError("Confirmed source asset does not belong to this timeline project")
        if not asset.audio_key:
            raise SubtitleGenerationError("Source asset has no preprocessed audio")
        publish_project_status(str(timeline.project_id), progress=10, stage="subtitle_preparing", message="正在準備字幕音訊", job_id=self.request.id)

        segments = [ConfirmedTimelineSegment.model_validate(segment) for segment in confirmed.get("segments", [])]
        keep_segments = sorted((segment for segment in segments if segment.action == "keep"), key=lambda segment: segment.source_start)
        if not keep_segments:
            raise SubtitleGenerationError("Confirmed timeline has no keep segments")

        language = confirmed.get("language")
        glossary = [AcademicGlossaryEntry.model_validate(item) for item in dict(timeline.settings_json or {}).get("academic_glossary", [])]
        cues: list[SubtitleCue] = []
        output_offset = 0.0
        with tempfile.TemporaryDirectory(prefix=f"subtitles-{timeline_id}-") as temp_dir:
            workdir = Path(temp_dir)
            source_audio = workdir / "source-audio.wav"
            download_object(asset.audio_key, str(source_audio))
            for index, segment in enumerate(keep_segments):
                clip_audio = workdir / f"keep-{index:04d}.wav"
                _trim_audio(source_audio, clip_audio, segment.source_start, segment.source_end)
                progress = 20 + int(index / len(keep_segments) * 65)
                publish_project_status(str(timeline.project_id), progress=progress, stage="subtitle_transcribing", message="正在精確轉錄保留片段", job_id=self.request.id)
                transcript = get_asr_provider().transcribe(
                    str(clip_audio), language=language, word_timestamps=True
                )
                annotate_transcript_kinetics(transcript)
                segment_cues = transcript_to_cues(transcript, output_offset, len(cues) + 1)
                cues.extend(segment_cues)
                output_offset += segment.source_end - segment.source_start

        cues, glossary_review = apply_glossary_to_cues(cues, glossary)

        base_key = f"projects/{timeline.project_id}/timelines/{timeline.id}/subtitles"
        srt_key = f"{base_key}/subtitles.srt"
        ass_key = f"{base_key}/subtitles.ass"
        kinetic_webm_key: str | None = None
        publish_project_status(str(timeline.project_id), progress=90, stage="subtitle_uploading", message="正在儲存字幕檔案", job_id=self.request.id)
        upload_bytes(srt_key, cues_to_srt(cues).encode("utf-8"), "application/x-subrip")
        caption_style = dict(timeline.settings_json.get("caption_style", {}))
        preset = str(caption_style.get("preset", "viral_yellow"))
        aspect_ratio = str(caption_style.get("aspect_ratio", "9:16"))
        upload_bytes(ass_key, cues_to_ass(cues, preset=preset, aspect_ratio=aspect_ratio).encode("utf-8"), "text/x-ssa")
        if settings.kinetic_subtitle_webm:
            with tempfile.TemporaryDirectory(prefix=f"kinetic-subtitles-{timeline_id}-") as render_dir:
                kinetic_path = Path(render_dir) / "kinetic-captions.webm"
                render_kinetic_webm(cues, kinetic_path)
                kinetic_webm_key = f"{base_key}/kinetic-captions.webm"
                upload_object(kinetic_webm_key, str(kinetic_path), "video/webm")

        queued_subtitles = dict(timeline.settings_json.get("subtitles", {}))
        target_language = queued_subtitles.get("target_language")
        bilingual: dict[str, Any] | None = None
        if target_language:
            publish_project_status(str(timeline.project_id), progress=84, stage="subtitle_translating", message="正在依影片脈絡翻譯雙語字幕", job_id=self.request.id)
            bilingual = _create_bilingual_artifacts(
                timeline=timeline, cues=cues, source_language=language, target_language=str(target_language),
                caption_style=caption_style, glossary=[item.model_dump(mode="json") for item in glossary],
            )
        timeline.settings_json = {
            **timeline.settings_json,
            "subtitles": {
                "language": language,
                "items": [cue.model_dump(mode="json") for cue in cues],
                "srt_key": srt_key,
                "ass_key": bilingual["ass_key"] if bilingual else ass_key,
                "kinetic_webm_key": kinetic_webm_key,
                "render_mode": "ass",
                "caption_preset": preset,
                "caption_aspect_ratio": aspect_ratio,
                "academic_glossary_review": glossary_review,
                "status": "completed",
            },
            "bilingual_subtitles": bilingual,
        }
        db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="subtitle_completed", status="completed", message="字幕生成完成", job_id=self.request.id)
        return {"timeline_id": timeline_id, "cue_count": len(cues), "srt_key": srt_key, "ass_key": bilingual["ass_key"] if bilingual else ass_key, "bilingual": bool(bilingual)}
    except Exception as exc:
        db.rollback()
        if timeline is not None and is_retryable_ai_error(exc):
            retry_ai_task(self, exc, project_id=str(timeline.project_id), stage="subtitle_transcribing", message="AI 轉錄服務暫時不可用", job_id=self.request.id)
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {
                    **current.settings_json,
                    "subtitles": {"status": "failed", "error": str(exc)},
                }
                db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="subtitle_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="subtitle.generate_bilingual_subtitles_for_timeline")
def generate_bilingual_subtitles_for_timeline(
    self, timeline_id: str, target_language: str, source_language: str | None = None,
) -> dict[str, Any]:
    """Add a translated subtitle track to an already transcribed timeline."""
    db = SessionLocal()
    timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise SubtitleGenerationError(f"Timeline {timeline_id} not found")
        settings_json = dict(timeline.settings_json or {})
        subtitles = dict(settings_json.get("subtitles", {}))
        if subtitles.get("status") != "completed":
            raise SubtitleGenerationError("Generate timestamped source subtitles before translating")
        cues = [SubtitleCue.model_validate(item) for item in subtitles.get("items", [])]
        if not cues:
            raise SubtitleGenerationError("No source subtitle cues are available")
        glossary = [item.model_dump(mode="json") for item in [AcademicGlossaryEntry.model_validate(item) for item in settings_json.get("academic_glossary", [])]]
        publish_project_status(str(timeline.project_id), progress=20, stage="subtitle_translating", message="正在依影片脈絡翻譯雙語字幕", job_id=self.request.id)
        bilingual = _create_bilingual_artifacts(
            timeline=timeline, cues=cues, source_language=source_language or subtitles.get("language"),
            target_language=target_language, caption_style=dict(settings_json.get("caption_style", {})), glossary=glossary,
        )
        timeline.settings_json = {
            **settings_json,
            "subtitles": {**subtitles, "ass_key": bilingual["ass_key"], "render_mode": "ass"},
            "bilingual_subtitles": bilingual,
        }
        db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="bilingual_subtitle_completed", status="completed", message="雙語字幕與 CC 匯出檔已準備完成", job_id=self.request.id)
        return {"timeline_id": timeline_id, "target_language": target_language, "cue_count": len(cues), **{key: bilingual[key] for key in ("bilingual_srt_key", "bilingual_vtt_key", "source_srt_key", "source_vtt_key", "target_srt_key", "target_vtt_key", "ass_key")}}
    except Exception as exc:
        db.rollback()
        if timeline is not None and is_retryable_ai_error(exc):
            retry_ai_task(self, exc, project_id=str(timeline.project_id), stage="subtitle_translating", message="翻譯服務暫時不可用", job_id=self.request.id)
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "bilingual_subtitles": {"status": "failed", "error": str(exc)}}
                db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="bilingual_subtitle_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
