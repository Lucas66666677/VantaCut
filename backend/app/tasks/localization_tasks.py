"""Picture-lock multilingual cloning, pause-only retiming, and Wav2Lip/SadTalker handoff."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.ai.providers.factory import get_text_provider, get_voice_clone_provider
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import RenderJob, RenderStatus, Timeline, VoiceProfile, VoiceProfileStatus
from app.services.multilingual_dubbing import DubCue, DubbingError, build_background_preserving_mix_command, build_dub_audio_command, build_pause_stretch_command, build_timing_plan, run_lip_sync, translate_cues
from app.services.storage import download_object, upload_bytes, upload_object
from app.services.voice_cloning import VoiceProfileCipher, wav_duration_seconds
from app.worker import celery_app


def _duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True, timeout=60)
    return float(result.stdout.strip())


@celery_app.task(bind=True, name="localization.generate_dubbed_version")
def generate_dubbed_version(self, timeline_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id)); render = db.get(RenderJob, UUID(str(request["render_job_id"]))); profile = db.get(VoiceProfile, UUID(str(request["voice_profile_id"])))
        if timeline is None or render is None or profile is None or render.timeline_id != timeline.id or profile.project_id != timeline.project_id: raise DubbingError("Picture lock, timeline, or voice profile is invalid")
        if render.status != RenderStatus.COMPLETED or not render.output_key: raise DubbingError("A completed picture-lock render is required")
        if profile.status != VoiceProfileStatus.READY or not profile.conditioning_artifact_key: raise DubbingError("Voice profile is not ready")
        subtitles = dict(timeline.settings_json.get("subtitles", {})); cues = [DubCue(str(item["id"]), float(item["start_time"]), float(item["end_time"]), str(item["text"])) for item in subtitles.get("items", [])]
        if not cues: raise DubbingError("Generate subtitles before requesting a localized dub")
        target = str(request["target_language"]); publish_project_status(str(timeline.project_id), progress=8, stage="dub_translating", message=f"正在翻譯成 {target} 配音稿", job_id=self.request.id)
        translated = translate_cues(get_text_provider(), cues, target)
        with tempfile.TemporaryDirectory(prefix=f"dub-{timeline.id}-{target}-") as temporary:
            workdir = Path(temporary); picture, conditioning, artifact = workdir / "picture-lock.mp4", workdir / "conditioning.bin", workdir / "conditioning.pt"
            download_object(render.output_key, str(picture)); download_object(profile.conditioning_artifact_key, str(conditioning)); artifact.write_bytes(VoiceProfileCipher().decrypt(conditioning.read_bytes()))
            raw_paths: dict[str, Path] = {}; raw_durations: dict[str, float] = {}; provider = get_voice_clone_provider()
            for index, cue in enumerate(translated):
                raw = workdir / f"dub-{index:04d}.wav"; provider.synthesize(text=cue.text, language=target, artifact_path=str(artifact), output_wav=str(raw), emotion="neutral", tempo=1.0)
                raw_paths[cue.cue_id], raw_durations[cue.cue_id] = raw, wav_duration_seconds(raw)
                publish_project_status(str(timeline.project_id), progress=18 + int((index + 1) / len(translated) * 42), stage="dub_voice_cloning", message="正在以原講者音色生成外語配音", job_id=self.request.id)
            timing = build_timing_plan(translated, raw_durations); picture_duration = _duration(picture)
            retimed_picture, dubbed_audio, lip_synced = workdir / "retimed-picture.mp4", workdir / "dubbed.wav", workdir / "lip-synced.mp4"
            subprocess.run(build_pause_stretch_command(str(picture), translated, timing, str(retimed_picture), video_duration=picture_duration), check=True, capture_output=True, text=True, timeout=2*60*60)
            retimed_duration = _duration(retimed_picture)
            subprocess.run(build_dub_audio_command([(item, str(raw_paths[item.cue_id])) for item in timing], str(dubbed_audio), duration_seconds=retimed_duration), check=True, capture_output=True, text=True, timeout=60*60)
            background_key = timeline.settings_json.get("localized_background_audio_key")
            background_warning = None
            if request.get("preserve_background_audio", True):
                if background_key:
                    background, mixed = workdir / "background-bed.wav", workdir / "dubbed-with-background.wav"
                    download_object(str(background_key), str(background))
                    subprocess.run(build_background_preserving_mix_command(str(background), str(dubbed_audio), str(mixed), duration_seconds=retimed_duration), check=True, capture_output=True, text=True, timeout=60*60)
                    dubbed_audio = mixed
                else:
                    background_warning = "No dialogue-free music/SFX bed is configured; output replaces the full mixed source audio to avoid original-language dialogue bleed."
            publish_project_status(str(timeline.project_id), progress=72, stage="dub_lipsync", message="正在以外語配音重建講者嘴型", job_id=self.request.id)
            lip_report = run_lip_sync(str(request.get("lip_sync_provider", "wav2lip")), str(retimed_picture), str(dubbed_audio), str(lip_synced))
            base = f"projects/{timeline.project_id}/timelines/{timeline.id}/localized/{target}/{self.request.id}"; audio_key, video_key, transcript_key = f"{base}/dub.wav", f"{base}/lip-synced.mp4", f"{base}/translated-cues.json"
            upload_object(audio_key, str(dubbed_audio), "audio/wav"); upload_object(video_key, str(lip_synced), "video/mp4"); upload_bytes(transcript_key, json.dumps([{"cue_id": cue.cue_id, "text": cue.text} for cue in translated], ensure_ascii=False).encode(), "application/json")
        versions = list(timeline.settings_json.get("localized_versions", [])); versions.append({"language": target, "status": "completed", "video_key": video_key, "audio_key": audio_key, "translated_cues_key": transcript_key, "lip_sync": lip_report, "timing_plan": [item.__dict__ for item in timing], "background_audio_warning": background_warning})
        timeline.settings_json = {**dict(timeline.settings_json or {}), "localized_versions": versions[-30:]}; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="dub_completed", status="completed", message=f"{target} 多語配音版已完成，請審閱唇形同步", job_id=self.request.id)
        return {"timeline_id": timeline_id, "target_language": target, "video_key": video_key, "audio_key": audio_key}
    except Exception as exc:
        db.rollback()
        if timeline is not None: publish_project_status(str(timeline.project_id), progress=0, stage="dub_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally: db.close()
