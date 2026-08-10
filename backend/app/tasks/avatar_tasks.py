from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AvatarProfile, AvatarRenderJob, MediaAsset, MediaStatus, MediaType, Timeline
from app.services.avatar_animation import extract_pose_ik, get_audio2face_provider, write_animation_document
from app.services.avatar_renderer import render_avatar_rgba
from app.services.storage import download_object, upload_object
from app.worker import celery_app


def _output_time_for_source(timeline: Timeline, source_time: float) -> float:
    document = dict(timeline.settings_json or {}).get("confirmed_timeline", {})
    elapsed = 0.0
    for track in document.get("tracks", []):
        if track.get("type") != "main_video": continue
        for clip in track.get("clips", []):
            if clip.get("action", "keep") != "keep": continue
            start, end = float(clip["source_start"]), float(clip["source_end"])
            if start <= source_time <= end: return elapsed + source_time - start
            elapsed += end - start
    return source_time


@celery_app.task(bind=True, name="avatar.render_replacement")
def render_avatar_replacement(self, avatar_render_job_id: str) -> dict[str, str]:
    db = SessionLocal(); job = None
    try:
        job = db.get(AvatarRenderJob, UUID(avatar_render_job_id))
        if job is None: raise ValueError("Avatar render job not found")
        source, profile = db.get(MediaAsset, job.source_asset_id), db.get(AvatarProfile, job.avatar_profile_id)
        if source is None or profile is None: raise ValueError("Avatar profile or source asset not found")
        job.status, job.progress = "processing", 5; db.commit()
        publish_project_status(str(job.project_id), progress=5, stage="avatar_preparing", message="正在準備虛擬主播素材", job_id=str(job.id))
        with tempfile.TemporaryDirectory(prefix=f"avatar-{job.id}-") as temporary:
            workdir = Path(temporary); input_video, segment_video, audio, bundle = workdir / "source.mp4", workdir / "segment.mp4", workdir / "voice.wav", workdir / "avatar.bundle"
            animation, output = workdir / "animation.json", workdir / "avatar-alpha.mov"
            download_object(source.proxy_key or source.storage_key, str(input_video)); download_object(profile.asset_bundle_key, str(bundle))
            subprocess.run(["ffmpeg", "-y", "-ss", str(job.source_start), "-to", str(job.source_end), "-i", str(input_video), "-c:v", "libx264", "-c:a", "aac", str(segment_video)], check=True, capture_output=True, timeout=900)
            subprocess.run(["ffmpeg", "-y", "-i", str(segment_video), "-vn", "-ar", "16000", "-ac", "1", str(audio)], check=True, capture_output=True, timeout=300)
            publish_project_status(str(job.project_id), progress=25, stage="avatar_audio2face", message="正在生成唇形與 Blendshape 曲線", job_id=str(job.id))
            blendshapes = get_audio2face_provider().generate_blendshapes(audio)
            publish_project_status(str(job.project_id), progress=45, stage="avatar_motion_capture", message="正在將頭部與手勢重定向至角色骨架", job_id=str(job.id))
            motion = extract_pose_ik(segment_video)
            write_animation_document(animation, blendshapes=blendshapes, motion=motion, rig_mapping=dict(profile.rig_mapping_json or {}))
            publish_project_status(str(job.project_id), progress=62, stage="avatar_unreal_render", message="正在以 Unreal MRQ 產生透明虛擬主播", job_id=str(job.id))
            render_avatar_rgba(animation_path=animation, avatar_bundle_path=bundle, output_path=output, width=int(source.width or 1280), height=int(source.height or 720))
            base = f"projects/{job.project_id}/avatar/{job.id}"; blend_key, motion_key, rgba_key = f"{base}/blendshapes.json", f"{base}/motion.json", f"{base}/avatar-alpha.mov"
            upload_object(blend_key, str(animation), "application/json"); upload_object(motion_key, str(animation), "application/json"); upload_object(rgba_key, str(output), "video/quicktime")
        avatar_asset = MediaAsset(project_id=job.project_id, filename=f"avatar-{job.id}.mov", storage_key=rgba_key, media_type=MediaType.VIDEO, status=MediaStatus.READY, mime_type="video/quicktime", width=source.width, height=source.height, duration_seconds=job.source_end - job.source_start, metadata_json={"avatar_generated": True, "alpha": True, "audio_enabled": False, "provenance": "digital_avatar"})
        db.add(avatar_asset); db.flush()
        if job.timeline_id:
            timeline = db.get(Timeline, job.timeline_id)
            if timeline:
                settings = dict(timeline.settings_json or {}); document = dict(settings.get("confirmed_timeline", {})); tracks = list(document.get("tracks", [])); broll = next((track for track in tracks if track.get("type") == "b_roll"), None)
                if broll is None: broll = {"id": "avatar-overlay-track", "type": "b_roll", "z_index": 100, "clips": []}; tracks.insert(0, broll)
                broll["clips"] = [*list(broll.get("clips", [])), {"id": f"avatar-{job.id}", "source_asset_id": str(avatar_asset.id), "source_start": 0, "source_end": float(job.source_end - job.source_start), "timeline_start": _output_time_for_source(timeline, float(job.source_start)), "track": "b_roll", "z_index": 100, "audio_enabled": False, "action": "keep", "origin": "digital_avatar"}]
                document["tracks"] = tracks; settings["confirmed_timeline"] = document; settings["avatar_replacements"] = [*list(settings.get("avatar_replacements", [])), {"job_id": str(job.id), "asset_id": str(avatar_asset.id), "status": "completed", "disclosure": "digital_avatar"}]; timeline.settings_json = settings
        job.status, job.progress, job.blendshape_key, job.motion_key, job.rgba_video_key, job.output_asset_id = "completed", 100, blend_key, motion_key, rgba_key, avatar_asset.id
        job.provenance_json = {"disclosure": "digital_avatar", "audio_provider": blendshapes.get("provider"), "subject_consent": True}; db.commit()
        publish_project_status(str(job.project_id), progress=100, stage="avatar_completed", status="completed", message="虛擬主播已加入 B-Roll 覆蓋軌", job_id=str(job.id))
        return {"job_id": str(job.id), "rgba_video_key": rgba_key}
    except Exception as exc:
        db.rollback()
        if job:
            current = db.get(AvatarRenderJob, job.id)
            if current: current.status, current.error_message = "failed", str(exc); db.commit()
            publish_project_status(str(job.project_id), progress=0, stage="avatar_failed", status="failed", message=str(exc), job_id=str(job.id))
        raise
    finally: db.close()
