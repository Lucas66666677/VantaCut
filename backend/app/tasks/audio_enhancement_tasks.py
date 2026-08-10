import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import Clip
from app.services.audio_enhancement import NOISE_REDUCTION_FILTER, run_studio_sound_model
from app.services.storage import download_object, upload_object
from app.services.non_destructive import append_filter_layer
from app.worker import celery_app


ENHANCEMENT_TIMEOUT_SECONDS = 15 * 60


class AudioEnhancementError(RuntimeError):
    pass


def _sync_timeline_clip_effect(
    settings: dict,
    clip_id: str,
    audio_effects: list[str],
    enhanced_audio_key: str,
) -> dict:
    """Persist a canonical effect map and update any saved multitrack JSON copies."""
    effect_map = dict(settings.get("clip_audio_effects", {}))
    effect_map[clip_id] = {"audio_effects": audio_effects, "enhanced_audio_key": enhanced_audio_key}
    updated = {**settings, "clip_audio_effects": effect_map}
    for document_key in ("multitrack_timeline", "confirmed_timeline"):
        document = updated.get(document_key)
        if not isinstance(document, dict):
            continue
        for track in document.get("tracks", []):
            for timeline_clip in track.get("clips", []):
                if str(timeline_clip.get("id")) == clip_id:
                    timeline_clip["audio_effects"] = audio_effects
    return updated


def _sync_studio_sound_effect(settings: dict, clip_id: str, audio_effects: list[str], enhanced_audio_key: str, wet_mix: int, engine: str) -> dict:
    updated = _sync_timeline_clip_effect(settings, clip_id, audio_effects, enhanced_audio_key)
    effect_map = dict(updated.get("clip_audio_effects", {})); entry = dict(effect_map.get(clip_id, {}))
    entry["studio_sound"] = {"status": "completed", "enhanced_audio_key": enhanced_audio_key, "wet_mix": wet_mix, "engine": engine}
    effect_map[clip_id] = entry
    return {**updated, "clip_audio_effects": effect_map}


@celery_app.task(bind=True, name="audio.enhance_audio")
def enhance_audio(self, clip_id: str) -> dict[str, str]:
    """Create a denoised WAV preview for one clip and mark its final-render audio effects."""
    db = SessionLocal()
    clip: Clip | None = None
    try:
        clip = db.get(Clip, UUID(clip_id))
        if clip is None:
            raise AudioEnhancementError("Clip not found")
        asset = clip.source_asset
        if not asset.audio_key:
            raise AudioEnhancementError("Source media has no extracted audio")

        publish_project_status(str(clip.timeline.project_id), progress=10, stage="audio_enhancement_downloading", message="正在準備原始音訊", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"enhance-audio-{clip_id}-") as temp_dir:
            workdir = Path(temp_dir)
            source_audio = workdir / "source.wav"
            enhanced_audio = workdir / "noise-reduced.wav"
            download_object(asset.audio_key, str(source_audio))

            command = [
                "ffmpeg", "-y", "-i", str(source_audio),
                "-ss", f"{float(clip.source_start):.3f}",
                "-t", f"{float(clip.source_end - clip.source_start):.3f}",
                "-vn", "-ac", "1", "-ar", "48000", "-af", NOISE_REDUCTION_FILTER,
                "-c:a", "pcm_s16le", str(enhanced_audio),
            ]
            publish_project_status(str(clip.timeline.project_id), progress=45, stage="audio_enhancement_processing", message="正在降噪與人聲增強", job_id=self.request.id)
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=ENHANCEMENT_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                raise AudioEnhancementError("Audio enhancement timed out") from exc
            except subprocess.CalledProcessError as exc:
                raise AudioEnhancementError(f"Audio enhancement failed: {(exc.stderr or '')[-2000:]}") from exc

            enhanced_key = f"projects/{clip.timeline.project_id}/timelines/{clip.timeline_id}/audio/{clip.id}-noise-reduced.wav"
            publish_project_status(str(clip.timeline.project_id), progress=85, stage="audio_enhancement_uploading", message="正在儲存降噪預覽檔", job_id=self.request.id)
            upload_object(enhanced_key, str(enhanced_audio), "audio/wav")

        effects = list(dict.fromkeys([*clip.audio_effects, "noise_reduction"]))
        clip.audio_effects = effects
        settings = _sync_timeline_clip_effect(
            dict(clip.timeline.settings_json or {}), str(clip.id), effects, enhanced_key
        )
        clip.timeline.settings_json = append_filter_layer(
            settings,
            kind="audio_enhancement",
            target={"clip_id": str(clip.id)},
            parameters={"audio_effects": effects, "enhanced_audio_key": enhanced_key},
        )
        db.commit()
        publish_project_status(str(clip.timeline.project_id), progress=100, stage="audio_enhancement_completed", status="completed", message="AI 降噪完成", job_id=self.request.id)
        return {"clip_id": str(clip.id), "enhanced_audio_key": enhanced_key, "status": "completed"}
    except Exception as exc:
        db.rollback()
        if clip is not None:
            publish_project_status(str(clip.timeline.project_id), progress=0, stage="audio_enhancement_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="audio.enhance_studio_sound")
def enhance_studio_sound(self, clip_id: str, wet_mix: int = 72) -> dict[str, str | int]:
    """Produce an AI/DSP studio voice stem; final render applies the user-selected dry/wet amount."""
    db = SessionLocal(); clip: Clip | None = None
    try:
        clip = db.get(Clip, UUID(clip_id))
        if clip is None or not clip.source_asset.audio_key:
            raise AudioEnhancementError("Clip with extracted audio is required")
        wet_mix = max(0, min(100, int(wet_mix)))
        project_id = str(clip.timeline.project_id)
        publish_project_status(project_id, progress=8, stage="studio_sound_downloading", message="正在準備原始錄音", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"studio-sound-{clip.id}-") as temporary:
            workdir = Path(temporary); source, segment, enhanced = workdir / "source.wav", workdir / "segment.wav", workdir / "studio.wav"
            download_object(clip.source_asset.audio_key, str(source))
            try:
                subprocess.run(["ffmpeg", "-y", "-ss", f"{float(clip.source_start):.3f}", "-t", f"{float(clip.source_end - clip.source_start):.3f}", "-i", str(source), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(segment)], check=True, capture_output=True, text=True, timeout=ENHANCEMENT_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                raise AudioEnhancementError("Studio Sound segment extraction timed out") from exc
            except subprocess.CalledProcessError as exc:
                raise AudioEnhancementError(f"Studio Sound segment extraction failed: {(exc.stderr or '')[-2000:]}") from exc
            publish_project_status(project_id, progress=42, stage="studio_sound_processing", message="正在分離人聲、抑制風噪與殘響", job_id=self.request.id)
            engine = run_studio_sound_model(input_wav=segment, output_wav=enhanced)
            key = f"projects/{clip.timeline.project_id}/timelines/{clip.timeline_id}/audio/{clip.id}-studio-sound.wav"
            publish_project_status(project_id, progress=84, stage="studio_sound_uploading", message="正在儲存錄音室音質預覽檔", job_id=self.request.id)
            upload_object(key, str(enhanced), "audio/wav")
        effects = list(dict.fromkeys([*clip.audio_effects, "studio_sound"])); clip.audio_effects = effects
        settings = _sync_studio_sound_effect(dict(clip.timeline.settings_json or {}), str(clip.id), effects, key, wet_mix, engine)
        clip.timeline.settings_json = append_filter_layer(
            settings,
            kind="studio_sound",
            target={"clip_id": str(clip.id)},
            parameters={"audio_effects": effects, "enhanced_audio_key": key, "wet_mix": wet_mix, "engine": engine},
        )
        db.commit()
        publish_project_status(project_id, progress=100, stage="studio_sound_completed", status="completed", message="Studio Sound 完成，已可調整乾濕比例", job_id=self.request.id)
        return {"clip_id": str(clip.id), "enhanced_audio_key": key, "wet_mix": wet_mix, "engine": engine}
    except Exception as exc:
        db.rollback()
        if clip is not None:
            publish_project_status(str(clip.timeline.project_id), progress=0, stage="studio_sound_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
