"""Celery task for preparing a target-duration BGM remix without mutating the source."""
from __future__ import annotations

import tempfile
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, Timeline
from app.services.smart_audio_remix import SmartAudioRemixError, build_remix_command, estimate_music_sections, plan_smart_remix, serialise_sections
from app.services.storage import download_object, upload_object
from app.worker import celery_app


@celery_app.task(bind=True, name="smart_audio_remix.generate")
def generate_smart_audio_remix(self, timeline_id: str, bgm_asset_id: str, options: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline, bgm = db.get(Timeline, UUID(timeline_id)), db.get(MediaAsset, UUID(bgm_asset_id))
        if timeline is None or bgm is None or bgm.project_id != timeline.project_id:
            raise SmartAudioRemixError("Selected BGM is unavailable")
        publish_project_status(str(timeline.project_id), progress=12, stage="smart_remix_preparing", message="正在載入背景音樂", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"smart-remix-{timeline.id}-") as temporary:
            workdir = Path(temporary); source, output = workdir / "bgm-source", workdir / "smart-remix.m4a"
            download_object(bgm.audio_key or bgm.storage_key, str(source))
            publish_project_status(str(timeline.project_id), progress=38, stage="smart_remix_analyzing", message="正在分析 BPM、前奏、副歌與尾奏", job_id=self.request.id)
            music, sections, _ = estimate_music_sections(source)
            plan = plan_smart_remix(sections=sections, target_duration=float(options["target_duration_seconds"]), bpm=music.tempo_bpm)
            publish_project_status(str(timeline.project_id), progress=64, stage="smart_remix_stitching", message="正在依節拍交叉淡化並重組音樂", job_id=self.request.id)
            try:
                subprocess.run(build_remix_command(input_path=str(source), plan=plan, output_path=str(output)), check=True, capture_output=True, text=True, timeout=20 * 60)
            except subprocess.TimeoutExpired as exc:
                raise SmartAudioRemixError("BGM remix timed out") from exc
            except (subprocess.CalledProcessError, OSError) as exc:
                detail = getattr(exc, "stderr", "") or str(exc)
                raise SmartAudioRemixError(f"BGM remix failed: {detail[-1500:]}") from exc
            key = f"projects/{timeline.project_id}/timelines/{timeline.id}/smart-audio-remix/{self.request.id}.m4a"
            upload_object(key, str(output), "audio/mp4")
        settings = dict(timeline.settings_json or {}); confirmed = dict(settings.get("confirmed_timeline", {}))
        track = {"id": "smart-audio-remix", "type": "audio_overlay", "z_index": 15, "clips": [{"id": "smart-audio-remix-1", "kind": "smart_audio_remix", "audio_key": key, "timeline_start": 0, "source_start": 0, "source_end": plan["target_duration"], "action": "keep", "audio_enabled": True, "reason": "AI beat-aligned intro + chorus + outro remix"}]}
        if confirmed:
            settings["confirmed_timeline"] = {**confirmed, "tracks": [item for item in confirmed.get("tracks", []) if item.get("id") != track["id"]] + [track]}
        settings["smart_audio_remix"] = {"status": "completed", "bgm_asset_id": str(bgm.id), "remixed_audio_key": key, "target_duration_seconds": plan["target_duration"], "mix_level": float(options.get("mix_level", .16)), "bpm": plan["bpm"], "sections": serialise_sections(sections), "plan": plan, "track": track}
        timeline.settings_json = settings; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="smart_remix_ready", status="completed", message="背景音樂已依影片長度重新編曲並完美收尾", job_id=self.request.id, extra={"timeline_id": str(timeline.id), "bpm": plan["bpm"]})
        return {"timeline_id": str(timeline.id), "audio_key": key, "bpm": plan["bpm"]}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            settings = dict(timeline.settings_json or {}); settings["smart_audio_remix"] = {"status": "failed", "error": str(exc)}; timeline.settings_json = settings; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="smart_remix_failed", status="failed", message="智慧音樂重混失敗", job_id=self.request.id)
        raise
    finally:
        db.close()
