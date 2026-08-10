"""Generate built-in-style narration plus time-aligned subtitle/audio tracks."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.ai.providers.factory import get_narration_tts_provider
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import Timeline
from app.schemas.subtitle import SubtitleCue
from app.services.narration_tts import NARRATION_STYLES, apply_pitch_shift, narration_cues, wav_duration_seconds
from app.services.storage import upload_object, upload_bytes
from app.services.subtitles import cues_to_ass, cues_to_srt
from app.worker import celery_app


@celery_app.task(bind=True, name="narration.generate_tts")
def generate_tts_narration(self, timeline_id: str, narration_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise ValueError("Timeline not found")
        style = NARRATION_STYLES[str(request["style"])]
        publish_project_status(str(timeline.project_id), progress=15, stage="tts_synthesizing", message="正在生成 AI 旁白", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"tts-{timeline.id}-") as temporary:
            workdir = Path(temporary); raw, final = workdir / "raw.wav", workdir / "narration.wav"
            provider = get_narration_tts_provider()
            synthesis = provider.synthesize_narration(text=str(request["text"]), voice=style["voice"], instructions=style["instructions"], speed=float(request["speed"]), output_wav=str(raw))
            apply_pitch_shift(raw, final, semitones=float(request["pitch_semitones"]))
            duration = wav_duration_seconds(final); start = float(request["timeline_start"])
            cues = narration_cues(str(request["text"]), start_time=start, duration=duration, id_prefix=f"tts-{narration_id}")
            base = f"projects/{timeline.project_id}/timelines/{timeline.id}/narrations/{narration_id}"
            audio_key, srt_key, ass_key = f"{base}/narration.wav", f"{base}/subtitles.srt", f"{base}/subtitles.ass"
            upload_object(audio_key, str(final), "audio/wav")
            upload_bytes(srt_key, cues_to_srt(cues).encode("utf-8"), "application/x-subrip")
            upload_bytes(ass_key, cues_to_ass(cues, preset=str(request["caption_preset"])).encode("utf-8"), "text/x-ssa")
        settings = dict(timeline.settings_json or {}); narrations = list(settings.get("tts_narrations", []))
        completed = {"id": narration_id, "status": "completed", "text": request["text"], "style": request["style"], "speed": request["speed"], "pitch_semitones": request["pitch_semitones"], "timeline_start": start, "duration": round(duration, 3), "audio_key": audio_key, "srt_key": srt_key, "ass_key": ass_key, "cues": [cue.model_dump(mode="json") for cue in cues], "synthesis": synthesis}
        narrations = [completed if item.get("id") == narration_id else item for item in narrations]
        confirmed = dict(settings.get("confirmed_timeline", {})); tracks = list(confirmed.get("tracks", [])); track_id = "tts-narration-audio"
        audio_track = next((track for track in tracks if track.get("id") == track_id), {"id": track_id, "type": "audio_overlay", "z_index": 30, "clips": []})
        audio_track["clips"] = [clip for clip in audio_track.get("clips", []) if clip.get("id") != narration_id] + [{"id": narration_id, "kind": "tts_narration", "audio_key": audio_key, "source_start": 0, "source_end": round(duration, 3), "timeline_start": start, "action": "keep", "audio_enabled": True}]
        tracks = [track for track in tracks if track.get("id") != track_id] + [audio_track]
        if confirmed: settings["confirmed_timeline"] = {**confirmed, "tracks": tracks}
        effects = [track for track in settings.get("effect_tracks", []) if track.get("id") != f"tts-captions-{narration_id}"]
        effects.append({"id": f"tts-captions-{narration_id}", "type": "text_overlay", "z_index": 75, "items": completed["cues"], "ass_key": ass_key})
        timeline.settings_json = {**settings, "tts_narrations": narrations, "effect_tracks": effects}; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="tts_ready", status="completed", message="AI 旁白與同步字幕已加入時間軸", job_id=self.request.id)
        return {"timeline_id": timeline_id, "narration_id": narration_id, "audio_key": audio_key, "duration": duration}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            settings = dict(timeline.settings_json or {}); settings["tts_narrations"] = [{**item, "status": "failed", "error": str(exc)} if item.get("id") == narration_id else item for item in settings.get("tts_narrations", [])]; timeline.settings_json = settings; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="tts_failed", status="failed", message="AI 旁白生成失敗，請重試", job_id=self.request.id)
        raise
    finally:
        db.close()
