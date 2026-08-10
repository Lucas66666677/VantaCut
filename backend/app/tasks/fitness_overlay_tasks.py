"""Run rep detection in Celery and persist a render-ready fitness HUD document."""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline
from app.services.fitness_reps import analyze_repetitions, map_reps_to_timeline
from app.services.storage import download_object
from app.worker import celery_app


@celery_app.task(bind=True, name="fitness.analyze_reps")
def analyze_fitness_reps(self, timeline_id: str, request: dict[str, Any]) -> dict[str, object]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id)); source = db.get(MediaAsset, UUID(str(request["source_asset_id"])))
        if timeline is None or source is None or source.project_id != timeline.project_id or source.status != MediaStatus.READY or source.media_type != MediaType.VIDEO: raise ValueError("Choose a ready project video for fitness analysis")
        project_id = str(timeline.project_id); settings = dict(timeline.settings_json or {}); settings["fitness_overlay"] = {"status": "processing"}; timeline.settings_json = settings; db.commit()
        publish_project_status(project_id, progress=8, stage="fitness_pose_preparing", message="正在準備運動姿勢分析", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"fitness-{timeline.id}-") as temporary:
            local_video = Path(temporary) / "fitness-source.mp4"; download_object(source.proxy_key or source.storage_key, str(local_video))
            publish_project_status(project_id, progress=30, stage="fitness_pose_tracking", message="MediaPipe 正在追蹤關節週期", job_id=self.request.id)
            events = analyze_repetitions(local_video, exercise=str(request.get("exercise", "squat")), sample_every_n_frames=int(request.get("sample_every_n_frames", 3)), fatigue_ratio=float(request.get("fatigue_ratio", 1.25)))  # type: ignore[arg-type]
        document = copy.deepcopy(dict(settings.get("confirmed_timeline", {})))
        if "tracks" not in document:
            document["tracks"] = [{"id": "main-video", "type": "main_video", "z_index": 0, "clips": list(document.get("segments", []))}]
        mapped = map_reps_to_timeline(events, document, str(source.id))
        if not mapped: raise ValueError("沒有在目前保留的主影片片段中偵測到完整動作；請確認素材、景別與時間軸")
        fatigue_event = next((event for event in reversed(mapped) if bool(event.get("fatigue"))), None)
        target_reps, hud_style = int(request.get("target_reps", 10)), str(request.get("hud_style", "impact"))
        effect_track = {"id": "fitness-hud", "type": "effect_overlay", "z_index": 75, "clips": [{"id": f"fitness-rep-{event['rep']}", "kind": "fitness_rep_pop", "timeline_start": event["timeline_time"], "source_start": 0, "source_end": .7, "action": "keep", "reason": f"偵測到第 {event['rep']} 次 {request.get('exercise', 'squat')}"} for event in mapped]}
        if fatigue_event: effect_track["clips"].append({"id": "fitness-fatigue-finale", "kind": "fitness_fatigue_finale", "timeline_start": fatigue_event["timeline_time"], "source_start": 0, "source_end": 1.0, "action": "keep", "reason": "最後一組動作放慢，觸發燃燒高光"})
        tracks = [track for track in document.get("tracks", []) if isinstance(track, dict) and track.get("id") not in {effect_track["id"], "fitness-sfx"}] + [effect_track]
        # This semantic audio track documents the synthetic bass hit for editor UIs; rendering uses a lavfi source.
        if fatigue_event: tracks.append({"id": "fitness-sfx", "type": "audio_overlay", "z_index": 76, "clips": [{"id": "fitness-bass-hit", "kind": "synthetic_fitness_bass", "timeline_start": fatigue_event["timeline_time"], "source_start": 0, "source_end": .55, "action": "keep", "audio_enabled": True, "reason": "力竭高光重低音"}]})
        document["tracks"] = tracks
        overlay = {"status": "completed", "exercise": str(request.get("exercise", "squat")), "hud_style": hud_style, "target_reps": target_reps, "events": mapped, "fatigue_event": fatigue_event, "bass_hit": {"timeline_start": float(fatigue_event["timeline_time"]), "duration": .55, "frequency_hz": 48} if fatigue_event else None}
        timeline.settings_json = {**settings, "confirmed_timeline": document, "multitrack_timeline": document, "fitness_overlay": overlay}; db.commit()
        publish_project_status(project_id, progress=100, stage="fitness_overlay_ready", status="completed", message=f"已偵測 {len(mapped)} 次動作並建立運動儀表板", job_id=self.request.id, extra={"timeline_id": str(timeline.id), "rep_count": len(mapped), "fatigue": bool(fatigue_event)})
        return {"timeline_id": str(timeline.id), "rep_count": len(mapped), "fatigue": bool(fatigue_event)}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None: current.settings_json = {**dict(current.settings_json or {}), "fitness_overlay": {"status": "failed", "error": str(exc)}}; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="fitness_overlay_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally: db.close()
