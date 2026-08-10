from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline
from app.services.audio_sync import AudioSyncError, estimate_audio_offset, extract_sync_wav
from app.services.auto_pip import speech_focus_events
from app.services.non_destructive import append_filter_layer
from app.services.storage import download_object
from app.tasks.matting_tasks import generate_video_matte
from app.worker import celery_app


@celery_app.task(bind=True, name="auto_pip.configure")
def configure_auto_pip(self, timeline_id: str, main_asset_id: str, selfie_asset_id: str, options: dict[str, object]) -> dict[str, object]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline, main, selfie = db.get(Timeline, UUID(timeline_id)), db.get(MediaAsset, UUID(main_asset_id)), db.get(MediaAsset, UUID(selfie_asset_id))
        if timeline is None or main is None or selfie is None or main.project_id != timeline.project_id or selfie.project_id != timeline.project_id or main.status != MediaStatus.READY or selfie.status != MediaStatus.READY or main.media_type != MediaType.VIDEO or selfie.media_type != MediaType.VIDEO:
            raise ValueError("Main and selfie videos must be ready project video assets")
        project_id = str(timeline.project_id)
        publish_project_status(project_id, progress=10, stage="auto_pip_downloading", message="正在載入主畫面與自拍鏡頭", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"auto-pip-{timeline.id}-") as temporary:
            workdir = Path(temporary); main_path, selfie_path = workdir / "main.mp4", workdir / "selfie.mp4"
            download_object(main.proxy_key or main.storage_key, str(main_path)); download_object(selfie.proxy_key or selfie.storage_key, str(selfie_path))
            publish_project_status(project_id, progress=35, stage="auto_pip_aligning", message="正在以聲學特徵對齊兩個鏡頭", job_id=self.request.id)
            try:
                main_wav, selfie_wav = workdir / "main.wav", workdir / "selfie.wav"
                extract_sync_wav(main_path, main_wav); extract_sync_wav(selfie_path, selfie_wav)
                offset = estimate_audio_offset(main_wav, selfie_wav)
                offset_seconds, sync_confidence = offset.offset_seconds, offset.confidence
            except AudioSyncError:
                offset_seconds, sync_confidence = 0.0, 0.0
            publish_project_status(project_id, progress=58, stage="auto_pip_voice_focus", message="正在偵測連續解說段落與焦點切換", job_id=self.request.id)
            focus_events = speech_focus_events(selfie_path, timeline_offset=offset_seconds, minimum_seconds=float(options.get("focus_after_seconds", 3)))
        publish_project_status(project_id, progress=72, stage="auto_pip_matting", message="正在建立自拍鏡頭去背遮罩", job_id=self.request.id)
        matte_task = generate_video_matte.delay(str(selfie.id), {"mode": "text", "text_prompt": "person", "frame_time": 0, "use_proxy": True, "feather_pixels": 2.5, "despill_strength": .65})
        duration = max(0.1, min(float(main.duration_seconds or 0), float(selfie.duration_seconds or 0) - max(0, -offset_seconds)))
        selfie_clip = {"id": "auto-pip-selfie", "kind": "auto_pip_selfie", "source_asset_id": str(selfie.id), "source_start": max(0, -offset_seconds), "source_end": max(0, -offset_seconds) + duration, "timeline_start": max(0, offset_seconds), "track": "b_roll", "z_index": 50, "audio_enabled": False, "action": "keep", "reason": "Auto-PiP selfie overlay with queued SAM 2 alpha matte"}
        track = {"id": "auto-pip-selfie-track", "type": "b_roll", "z_index": 50, "clips": [selfie_clip]}
        settings = dict(timeline.settings_json or {}); confirmed = dict(settings.get("confirmed_timeline", {}))
        if confirmed:
            confirmed["tracks"] = [item for item in confirmed.get("tracks", []) if item.get("id") != track["id"]] + [track]; settings["confirmed_timeline"] = confirmed
        auto_pip = {"status": "completed", "main_asset_id": str(main.id), "selfie_asset_id": str(selfie.id), "sync_offset_seconds": offset_seconds, "sync_confidence": sync_confidence, "corner": options.get("corner", "bottom_right"), "pip_layout": {"scale": .28, "padding": .04, "border_radius": .045}, "matting": {"status": "queued", "task_id": matte_task.id}, "focus_events": focus_events, "main_focus_effect": {"mode": "blur_and_scale", "blur_sigma": 8, "scale": .82}, "track": track, "overlays": list(dict(settings.get("auto_pip", {})).get("overlays", []))}
        settings["auto_pip"] = auto_pip
        timeline.settings_json = append_filter_layer(settings, kind="auto_pip", target={"timeline_id": str(timeline.id), "selfie_asset_id": str(selfie.id)}, parameters={"layout": auto_pip["pip_layout"], "focus_events": focus_events, "matting_task_id": matte_task.id}, source="ai")
        db.commit()
        publish_project_status(project_id, progress=100, stage="auto_pip_ready", status="completed", message="智慧畫中畫、去背任務與語音焦點切換已加入時間軸", job_id=self.request.id)
        return {"timeline_id": timeline_id, "focus_event_count": len(focus_events), "matting_task_id": matte_task.id}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            settings = dict(timeline.settings_json or {}); settings["auto_pip"] = {"status": "failed", "error": str(exc)}; timeline.settings_json = settings; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="auto_pip_failed", status="failed", message="智慧畫中畫設定失敗", job_id=self.request.id)
        raise
    finally:
        db.close()
