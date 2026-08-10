"""Background Meme/GIF preparation; all suggestions remain editable before render."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, Timeline
from app.services.meme_gifs import MemeGifError, detect_meme_triggers, download_gif, gif_to_webm, search_gif
from app.services.storage import upload_object
from app.worker import celery_app


@celery_app.task(bind=True, name="meme_gif.generate")
def generate_meme_gifs(self, timeline_id: str, source_asset_id: str, options: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline, asset = db.get(Timeline, UUID(timeline_id)), db.get(MediaAsset, UUID(source_asset_id))
        if timeline is None or asset is None or asset.project_id != timeline.project_id:
            raise ValueError("Meme GIF source video is unavailable")
        settings = dict(timeline.settings_json or {})
        settings["meme_gif"] = {"status": "processing", "events": []}; timeline.settings_json = settings; db.commit()
        analysis = db.scalar(select(AIAnalysis).where(AIAnalysis.media_asset_id == asset.id, AIAnalysis.analysis_type == AnalysisType.ROUGH_CUT, AIAnalysis.status == "completed").order_by(AIAnalysis.created_at.desc()))
        result = dict(analysis.result_json or {}) if analysis else {}
        triggers = detect_meme_triggers(transcript=dict(result.get("transcript") or {}), silences=list(result.get("silences") or []), limit=int(options.get("max_events", 4)))
        publish_project_status(str(timeline.project_id), progress=25, stage="meme_trigger_detection", message="正在找出無言停頓與迷因語氣", job_id=self.request.id)
        events: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix=f"meme-gif-{timeline.id}-") as temporary:
            workdir = Path(temporary)
            for index, trigger in enumerate(triggers):
                event = dict(trigger)
                try:
                    found = search_gif(query=str(event["query"]), provider=str(options.get("provider", "auto")))
                    gif, webm = workdir / f"{index}.gif", workdir / f"{index}.webm"
                    download_gif(found["url"], gif, found["provider"])
                    gif_to_webm(gif, webm, float(event["duration"]))
                    key = f"projects/{timeline.project_id}/timelines/{timeline.id}/meme-gifs/{event['id']}.webm"
                    upload_object(key, str(webm), "video/webm")
                    event.update({"status": "ready", "webm_key": key, "provider": found["provider"], "source_url": found["source_url"], "title": found["title"], "insertion_mode": str(options.get("insertion_mode", "overlay")), "attribution_required": True})
                except MemeGifError as exc:
                    # A missing API key/network failure must not remove the useful timeline suggestion.
                    event.update({"status": "suggested", "error": str(exc), "insertion_mode": str(options.get("insertion_mode", "overlay")), "attribution_required": True})
                events.append(event)
                publish_project_status(str(timeline.project_id), progress=30 + int((index + 1) / max(1, len(triggers)) * 55), stage="meme_gif_search", message=f"正在準備迷因素材 {index + 1}/{len(triggers)}", job_id=self.request.id)
        track = {"id": "meme-gif-overlays", "type": "effect_overlay", "z_index": 80, "clips": [{"id": event["id"], "source_start": 0, "source_end": event["duration"], "timeline_start": event["timeline_start"], "action": "keep", "reason": event["reason"], "status": event["status"]} for event in events]}
        confirmed = dict(settings.get("confirmed_timeline", {})); tracks = [item for item in confirmed.get("tracks", []) if item.get("id") != track["id"]] + [track]
        if confirmed:
            settings["confirmed_timeline"] = {**confirmed, "tracks": tracks}
        # Reuse an already selected project BGM by default, so the tape-stop can
        # affect the isolated BGM stem instead of muting the creator's dialogue.
        auto_sfx = dict(settings.get("auto_sfx", {})); one_click = dict(settings.get("one_click", {})); beat_sync = dict(settings.get("beat_sync_montage", {})); narrative = dict(settings.get("auto_narrative", {}))
        bgm_asset_id = options.get("bgm_asset_id") or auto_sfx.get("bgm_asset_id") or one_click.get("bgm_asset_id") or beat_sync.get("bgm_asset_id") or narrative.get("bgm_asset_id")
        settings["meme_gif"] = {"status": "completed", "events": events, "track": track, "bgm_asset_id": bgm_asset_id, "comedic_sfx_asset_id": options.get("comedic_sfx_asset_id"), "tape_stop_events": [{"timeline_start": item["timeline_start"], "duration": .22} for item in events if item.get("status") == "ready"]}
        timeline.settings_json = settings; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="meme_gif_ready", status="completed", message="迷因建議已加入效果軌，請審閱後導出", job_id=self.request.id, extra={"timeline_id": str(timeline.id), "event_count": len(events)})
        return {"timeline_id": str(timeline.id), "event_count": len(events)}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            settings = dict(timeline.settings_json or {}); settings["meme_gif"] = {"status": "failed", "events": [], "error": str(exc)}; timeline.settings_json = settings; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="meme_gif_failed", status="failed", message="迷因素材準備失敗", job_id=self.request.id)
        raise
    finally:
        db.close()
