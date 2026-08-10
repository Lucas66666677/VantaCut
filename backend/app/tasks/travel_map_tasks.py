"""Create a route animation MediaAsset and attach it to the Timeline's B-Roll/SFX tracks."""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline
from app.services.storage import upload_object
from app.services.travel_maps import TravelMapError, create_route_sfx, render_route_map_video, route_points_from_text
from app.worker import celery_app


def _document(confirmed: dict[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(confirmed)
    if "tracks" not in document:
        document["tracks"] = [{"id": "main-video", "type": "main_video", "z_index": 0, "clips": list(document.get("segments", []))}]
    return document


def _transcript_text(settings_json: dict[str, Any]) -> str:
    subtitles = list(dict(settings_json.get("subtitles", {})).get("items", []))
    transcript = list(dict(settings_json.get("transcript", {})).get("segments", []))
    return " ".join(str(item.get("text", "")) for item in subtitles + transcript if isinstance(item, dict))


@celery_app.task(bind=True, name="travel_map.generate")
def generate_travel_map(self, timeline_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None: raise TravelMapError("Timeline not found")
        project_id = str(timeline.project_id); settings_json = dict(timeline.settings_json or {})
        settings_json["travel_map"] = {"status": "processing"}; timeline.settings_json = settings_json; db.commit()
        publish_project_status(project_id, progress=10, stage="travel_map_extracting", message="正在從旁白提取旅行地點", job_id=self.request.id)
        route_text = str(request.get("route_text") or _transcript_text(settings_json)).strip()
        points = route_points_from_text(route_text)
        publish_project_status(project_id, progress=35, stage="travel_map_geocoding", message="正在定位旅行路線", job_id=self.request.id)
        duration, aspect_ratio, vehicle = float(request.get("duration_seconds", 4.0)), str(request.get("aspect_ratio", "9:16")), str(request.get("vehicle", "plane"))
        with tempfile.TemporaryDirectory(prefix=f"travel-map-{timeline.id}-") as temporary:
            workdir = Path(temporary); video_path, sfx_path = workdir / "route.mp4", workdir / "route-stinger.wav"
            publish_project_status(project_id, progress=55, stage="travel_map_rendering", message="正在繪製 3D 旅行地圖動畫", job_id=self.request.id)
            width, height = render_route_map_video(points=points, output_path=video_path, duration_seconds=duration, aspect_ratio=aspect_ratio, vehicle=vehicle)
            create_route_sfx(output_path=sfx_path, duration_seconds=duration, vehicle=vehicle)
            video_key = f"projects/{timeline.project_id}/travel-maps/{self.request.id}.mp4"; sfx_key = f"projects/{timeline.project_id}/travel-maps/{self.request.id}.wav"
            upload_object(video_key, str(video_path), "video/mp4"); upload_object(sfx_key, str(sfx_path), "audio/wav")
            video_asset = MediaAsset(project_id=timeline.project_id, filename="travel-route-map.mp4", storage_key=video_key, media_type=MediaType.VIDEO, status=MediaStatus.READY, mime_type="video/mp4", size_bytes=video_path.stat().st_size, duration_seconds=duration, width=width, height=height, fps=30, video_codec="h264", metadata_json={"generated": True, "generation_type": "travel_route_map", "vehicle": vehicle, "location_labels": [point.label for point in points]})
            sfx_asset = MediaAsset(project_id=timeline.project_id, filename="travel-route-stinger.wav", storage_key=sfx_key, media_type=MediaType.AUDIO, status=MediaStatus.READY, mime_type="audio/wav", size_bytes=sfx_path.stat().st_size, duration_seconds=min(duration, 1.2), audio_key=sfx_key, metadata_json={"generated": True, "generation_type": "travel_route_sfx", "vehicle": vehicle})
            db.add_all([video_asset, sfx_asset]); db.flush()
        confirmed = _document(dict(settings_json.get("confirmed_timeline", {})))
        broll = next((track for track in confirmed["tracks"] if track.get("type") == "b_roll"), None)
        if broll is None: broll = {"id": "travel-map-b-roll", "type": "b_roll", "z_index": 30, "clips": []}; confirmed["tracks"].append(broll)
        start = float(request.get("timeline_start") if request.get("timeline_start") is not None else 0)
        clip = {"id": f"travel-map-{self.request.id}", "source_asset_id": str(video_asset.id), "source_start": 0, "source_end": duration, "timeline_start": start, "track": "b_roll", "z_index": int(broll.get("z_index", 30)), "audio_enabled": False, "action": "keep", "kind": "travel_route_map", "fade_in_seconds": .2, "fade_out_seconds": .2, "confidence_score": 100, "reason": f"旅行路線：{' → '.join(point.label for point in points)}"}
        broll["clips"].append(clip)
        audio_track = next((track for track in confirmed["tracks"] if track.get("id") == "travel-map-sfx"), None)
        if audio_track is None: audio_track = {"id": "travel-map-sfx", "type": "audio_overlay", "z_index": 40, "clips": []}; confirmed["tracks"].append(audio_track)
        audio_track["clips"].append({"id": f"travel-map-sfx-{self.request.id}", "source_asset_id": str(sfx_asset.id), "source_start": 0, "source_end": min(duration, 1.2), "timeline_start": start, "track": "audio_overlay", "kind": f"travel_{vehicle}_stinger", "audio_enabled": True, "action": "keep", "reason": "自動生成的旅行路線提示音"})
        auto_sfx = dict(settings_json.get("auto_sfx", {})); events = list(auto_sfx.get("events", []))
        events.append({"id": f"travel-map-sfx-{self.request.id}", "kind": f"travel_{vehicle}_stinger", "timeline_start": start, "duration": min(duration, 1.2), "gain_db": -10.0, "source_asset_id": str(sfx_asset.id), "reason": "travel_route_map"})
        auto_sfx = {**auto_sfx, "status": "configured", "events": events}
        timeline.settings_json = {**settings_json, "confirmed_timeline": confirmed, "multitrack_timeline": confirmed, "auto_sfx": auto_sfx, "travel_map": {"status": "completed", "clip": clip, "route": [point.public_json() for point in points], "vehicle": vehicle}}
        db.commit()
        publish_project_status(project_id, progress=100, stage="travel_map_completed", status="completed", message="旅行地圖、路線音效已加入時間軸", job_id=self.request.id, extra={"timeline_id": str(timeline.id), "clip": clip})
        return {"timeline_id": str(timeline.id), "clip": clip}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "travel_map": {"status": "failed", "error": str(exc)}}; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="travel_map_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
