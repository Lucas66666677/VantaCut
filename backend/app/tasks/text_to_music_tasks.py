"""Generate original BGM asynchronously, enforce Timeline duration, and add it as an audio-only track."""
from __future__ import annotations

import tempfile
import math
from pathlib import Path
from typing import Any
from uuid import UUID

from app.ai.providers.factory import get_music_generation_provider
from app.core.config import settings
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import Timeline
from app.services.non_destructive import append_filter_layer
from app.services.storage import upload_object
from app.services.text_to_music import TextToMusicError, extract_accompaniment, finish_music
from app.worker import celery_app


@celery_app.task(bind=True, name="text_to_music.generate")
def generate_timeline_music(self, timeline_id: str, request: dict[str, Any], duration_seconds: float) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise TextToMusicError("Timeline not found")
        project_id, duration = str(timeline.project_id), max(1.0, float(duration_seconds))
        publish_project_status(project_id, progress=8, stage="music_generation_preparing", message="正在理解配樂氛圍與片長", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"generated-music-{timeline.id}-") as temporary:
            workdir = Path(temporary); generated, finished = workdir / "provider-audio.wav", workdir / "timeline-bgm.m4a"
            provider = get_music_generation_provider(request.get("provider"))
            publish_project_status(project_id, progress=24, stage="music_generation_generating", message="AI 正在譜寫原創配樂", job_id=self.request.id)
            metadata = provider.generate_music(prompt=str(request["prompt"]), duration_seconds=duration, instrumental=bool(request.get("instrumental_only", True)), output_path=str(generated))
            vocals_removed = False; source = generated
            if bool(request.get("instrumental_only", True)) and bool(metadata.get("has_vocals")):
                publish_project_status(project_id, progress=58, stage="music_generation_stems", message="偵測到人聲，正在分離純伴奏", job_id=self.request.id)
                source = extract_accompaniment(generated, workdir, command=settings.music_stem_separator_command, timeout_seconds=settings.music_generation_timeout_seconds)
                vocals_removed = True
            publish_project_status(project_id, progress=78, stage="music_generation_finishing", message="正在對齊影片時長並製作自然收尾", job_id=self.request.id)
            finishing = finish_music(source, finished, duration_seconds=duration, timeout_seconds=settings.music_generation_timeout_seconds)
            audio_key = f"projects/{timeline.project_id}/timelines/{timeline.id}/generated-music/{self.request.id}.m4a"
            upload_object(audio_key, str(finished), "audio/mp4")

        settings = dict(timeline.settings_json or {}); confirmed = dict(settings.get("confirmed_timeline", {})); tracks = list(confirmed.get("tracks", []))
        gain_db = round(20 * math.log10(max(.001, float(request.get("mix_level", .16)))), 3)
        track = {"id": "generated-music-bgm", "type": "audio_overlay", "z_index": 14, "clips": [{"id": "generated-music-bgm-1", "kind": "generated_music", "audio_key": audio_key, "timeline_start": 0, "source_start": 0, "source_end": round(duration, 3), "action": "keep", "audio_enabled": True, "audio_gain_db": gain_db, "reason": "AI-generated BGM, timeline-length locked with fade-out"}]}
        if confirmed:
            confirmed["tracks"] = [item for item in tracks if item.get("id") != track["id"]] + [track]
            settings["confirmed_timeline"] = confirmed
        multitrack = dict(settings.get("multitrack_timeline", {}))
        if multitrack:
            multi_tracks = list(multitrack.get("tracks", []))
            multitrack["tracks"] = [item for item in multi_tracks if item.get("id") != track["id"]] + [track]
            settings["multitrack_timeline"] = multitrack
        record = {"status": "completed", "prompt": request["prompt"], "target_duration_seconds": round(duration, 3), "instrumental_only": bool(request.get("instrumental_only", True)), "vocals_removed": vocals_removed, "audio_key": audio_key, "mix_level": float(request.get("mix_level", .16)), "provider": metadata, "provider_name": provider.name, "finishing_mode": finishing["mode"], "finishing": finishing, "track": track}
        settings["generated_music"] = record
        timeline.settings_json = append_filter_layer(settings, kind="generated_music", target={"timeline_id": str(timeline.id), "track_id": track["id"]}, parameters={"audio_key": audio_key, "target_duration_seconds": duration, "instrumental_only": record["instrumental_only"], "vocals_removed": vocals_removed}, source="ai")
        db.commit()
        publish_project_status(project_id, progress=100, stage="music_generation_ready", status="completed", message="原創配樂已對齊影片長度並加入 BGM 軌", job_id=self.request.id)
        return {"timeline_id": timeline_id, "audio_key": audio_key, "duration": duration, "vocals_removed": vocals_removed}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                existing = dict(current.settings_json or {}); previous = dict(existing.get("generated_music", {})); existing["generated_music"] = {**previous, "status": "failed", "error": str(exc)}; current.settings_json = existing; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="music_generation_failed", status="failed", message="AI 配樂生成失敗，請重試", job_id=self.request.id)
        raise
    finally:
        db.close()
