"""Analyze and attach a high-quality recorder track to a Timeline."""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline
from app.services.audio_sync import AudioSyncError, estimate_audio_offset, extract_sync_wav
from app.services.storage import download_object
from app.worker import celery_app


def _duration(document: dict) -> float:
    return sum(max(0.0, float(clip.get("source_end", 0)) - float(clip.get("source_start", 0))) for track in document.get("tracks", []) if isinstance(track, dict) and track.get("type") == "main_video" for clip in track.get("clips", []) if isinstance(clip, dict) and clip.get("action", "keep") == "keep")


@celery_app.task(bind=True, name="audio_sync.align_external_audio")
def align_external_audio(self, timeline_id: str, request: dict[str, object]) -> dict[str, object]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id)); video = db.get(MediaAsset, UUID(str(request["video_asset_id"]))); external = db.get(MediaAsset, UUID(str(request["external_audio_asset_id"])))
        if timeline is None or video is None or external is None or video.project_id != timeline.project_id or external.project_id != timeline.project_id or video.status != MediaStatus.READY or external.status != MediaStatus.READY or video.media_type != MediaType.VIDEO or external.media_type != MediaType.AUDIO: raise AudioSyncError("Choose a ready project video and a ready external audio asset")
        settings = dict(timeline.settings_json or {}); settings["audio_sync"] = {"status": "processing"}; timeline.settings_json = settings; db.commit(); project_id = str(timeline.project_id)
        publish_project_status(project_id, progress=12, stage="audio_sync_downloading", message="正在載入相機與高音質收音", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"audio-sync-{timeline.id}-") as temporary:
            workdir = Path(temporary); camera_source, external_source = workdir / "camera", workdir / "external"; camera_wav, external_wav = workdir / "camera.wav", workdir / "external.wav"
            download_object(video.proxy_key or video.storage_key, str(camera_source)); download_object(external.audio_key or external.storage_key, str(external_source))
            publish_project_status(project_id, progress=35, stage="audio_sync_features", message="正在提取聲學特徵並計算時間偏移", job_id=self.request.id)
            extract_sync_wav(camera_source, camera_wav); extract_sync_wav(external_source, external_wav)
            offset = estimate_audio_offset(camera_wav, external_wav, max_offset_seconds=float(request.get("max_offset_seconds", 120)))
        document = copy.deepcopy(dict(settings.get("confirmed_timeline", {})))
        if "tracks" not in document: document["tracks"] = [{"id": "main-video", "type": "main_video", "z_index": 0, "clips": list(document.get("segments", []))}]
        timeline_duration = _duration(document)
        if timeline_duration <= 0: raise AudioSyncError("Confirm a non-empty Timeline before audio synchronization")
        main_segments = [dict(item) for track in document["tracks"] if isinstance(track, dict) and track.get("type") == "main_video" for item in track.get("clips", []) if isinstance(item, dict) and item.get("action", "keep") == "keep" and str(item.get("source_asset_id", video.id)) == str(video.id)]
        if not main_segments: raise AudioSyncError("No kept main-video segments use the selected camera asset")
        first_external_time = float(main_segments[0]["source_start"]) - offset.offset_seconds
        clip = {"id": f"synced-audio-{self.request.id}", "source_asset_id": str(external.id), "source_start": round(max(0, first_external_time), 3), "source_end": round(max(0, first_external_time) + timeline_duration, 3), "timeline_start": 0, "track": "audio_overlay", "audio_enabled": True, "action": "keep", "kind": "synced_external_audio", "confidence_score": round(offset.confidence * 100, 1), "reason": f"FFT 聲學交叉比對已對齊（偏移 {offset.offset_seconds:+.3f}s）"}
        tracks = list(document["tracks"]); audio_track = next((track for track in tracks if track.get("id") == "synced-external-audio"), None)
        if audio_track is None: audio_track = {"id": "synced-external-audio", "type": "audio_overlay", "z_index": 25, "clips": []}; tracks.append(audio_track)
        audio_track["clips"] = [item for item in audio_track.get("clips", []) if item.get("kind") != "synced_external_audio"] + [clip]
        # Inform the editor and protect original camera audio. The render mixer replaces it with this source.
        for track in tracks:
            if track.get("type") != "main_video": continue
            for item in track.get("clips", []):
                if str(item.get("source_asset_id", video.id)) == str(video.id): item["audio_enabled"] = False; item["gain_db"] = -80
        document["tracks"] = tracks
        sync_segments = [{"source_start": float(item["source_start"]), "source_end": float(item["source_end"])} for item in main_segments]
        timeline.settings_json = {**settings, "confirmed_timeline": document, "multitrack_timeline": document, "audio_sync": {"status": "completed", "video_asset_id": str(video.id), "external_audio_asset_id": str(external.id), "offset_seconds": offset.offset_seconds, "confidence": offset.confidence, "audio_clip": clip, "segments": sync_segments, "muted_original_audio": True}}
        db.commit(); publish_project_status(project_id, progress=100, stage="audio_sync_completed", status="completed", message="高音質音軌已自動吸附，原始收音已靜音", job_id=self.request.id, extra={"timeline_id": str(timeline.id), "offset_seconds": offset.offset_seconds})
        return {"timeline_id": str(timeline.id), "offset_seconds": offset.offset_seconds, "confidence": offset.confidence}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None: current.settings_json = {**dict(current.settings_json or {}), "audio_sync": {"status": "failed", "error": str(exc)}}; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="audio_sync_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally: db.close()
