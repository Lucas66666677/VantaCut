"""Background construction of a reviewable profanity-censor effect track."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, Timeline
from app.services.profanity_filter import detect_profanity_words, map_output_events_to_source, track_mouth_positions
from app.services.storage import download_object
from app.worker import celery_app


@celery_app.task(bind=True, name="profanity.apply_filter")
def apply_profanity_filter(self, timeline_id: str, sfx_style: str, emoji_style: str) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None: raise ValueError("Timeline not found")
        settings = dict(timeline.settings_json or {}); subtitles = dict(settings.get("subtitles") or {})
        cues = [dict(item) for item in subtitles.get("items", []) if isinstance(item, dict)]
        if subtitles.get("status") != "completed" or not cues: raise ValueError("Generate timestamped subtitles before applying profanity filtering")
        publish_project_status(str(timeline.project_id), progress=20, stage="profanity_detecting", message="正在以 ASR 字級時間戳比對敏感詞", job_id=self.request.id)
        events = detect_profanity_words(cues)
        confirmed = dict(settings.get("confirmed_timeline") or {}); segments = list(confirmed.get("segments", []))
        if not segments:
            segments = [clip for track in confirmed.get("tracks", []) if track.get("type") == "main_video" for clip in track.get("clips", [])]
        events = map_output_events_to_source(events, [dict(item) for item in segments if isinstance(item, dict)])
        source_asset_id = confirmed.get("source_asset_id")
        asset = db.get(MediaAsset, UUID(str(source_asset_id))) if source_asset_id else None
        if events and asset is not None:
            publish_project_status(str(timeline.project_id), progress=48, stage="profanity_mouth_tracking", message="正在以 Face Mesh 追蹤嘴部位置", job_id=self.request.id)
            with tempfile.TemporaryDirectory(prefix=f"profanity-{timeline.id}-") as temp_dir:
                proxy_path = Path(temp_dir) / "source-proxy.mp4"; download_object(asset.proxy_key or asset.storage_key, str(proxy_path))
                events = track_mouth_positions(proxy_path, events)
        else:
            events = [{**event, "mouth_position": {"x": .5, "y": .63, "scale": .14, "tracking": "not_needed"}} for event in events]
        sticker_id = "censor_angry" if emoji_style == "angry" else "censor_duck"
        overlay_items = [{"id": f"{event['id']}-emoji", "sticker_id": sticker_id, "asset_url": f"/api/v1/sticker-library/{sticker_id}.webp", "label": "敏感詞遮擋", "fallback_emoji": "🤬" if emoji_style == "angry" else "🦆", "source_start": event["start_time"], "source_end": event["end_time"], "position": {"x": event["mouth_position"]["x"], "y": event["mouth_position"]["y"]}, "scale": max(.55, float(event["mouth_position"]["scale"]) * 5), "rotation": 0.0, "source": "ai", "enabled": True, "trigger": {"text": event["word"], "emotion": "anger"}, "confidence_score": round(float(event.get("confidence", 1)) * 100, 1)} for event in events]
        effect_track = {"id": "profanity-emoji-overlay", "type": "sticker_overlay", "z_index": 95, "enabled": True, "items": overlay_items}
        settings["effect_tracks"] = [item for item in settings.get("effect_tracks", []) if not (isinstance(item, dict) and item.get("id") == effect_track["id"])] + [effect_track]
        settings["profanity_filter"] = {"status": "completed", "sfx_style": sfx_style, "emoji_style": emoji_style, "events": events, "sfx_track": {"id": "profanity-sfx", "type": "audio_overlay", "z_index": 30, "clips": [{"id": f"{event['id']}-sfx", "timeline_start": event["start_time"], "source_start": 0, "source_end": event["end_time"] - event["start_time"], "action": "keep", "audio_enabled": True, "kind": sfx_style, "reason": f"Censor replacement for {event['word']}"} for event in events]}}
        timeline.settings_json = settings; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="profanity_completed", status="completed", message=f"已遮擋 {len(events)} 個敏感詞", job_id=self.request.id)
        return {"timeline_id": timeline_id, "event_count": len(events), "status": "completed"}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "profanity_filter": {"status": "failed", "error": str(exc)}}; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="profanity_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
