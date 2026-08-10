"""Celery jobs for cloud B-Roll generation and self-hosted temporal outpainting."""
from __future__ import annotations

import copy
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.ai_retry import is_retryable_ai_error, retry_ai_task
from app.core.config import settings
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline
from app.services.storage import create_download_url, download_object, upload_object
from app.services.video_generation import (
    VideoGenerationError,
    build_broll_prompt,
    get_video_generation_provider,
    get_video_outpaint_provider,
    select_broll_opportunity,
    timeline_source_time,
    visual_motion_score,
    write_edge_manifest,
)
from app.worker import celery_app


def _run(command: list[str], *, timeout: int = 60 * 10) -> None:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
        del completed
    except subprocess.TimeoutExpired as exc:
        raise VideoGenerationError("FFmpeg preprocessing timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise VideoGenerationError((exc.stderr or "FFmpeg preprocessing failed")[-2000:]) from exc


def _tracks_document(confirmed: dict[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(confirmed)
    if "tracks" not in document:
        document["tracks"] = [{"id": "main-video", "type": "main_video", "z_index": 0, "clips": list(document.get("segments", []))}]
    return document


@celery_app.task(bind=True, name="video.generate_broll")
def generate_broll(self, timeline_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None: raise VideoGenerationError("Timeline not found")
        source = db.get(MediaAsset, UUID(str(request["source_asset_id"])))
        if source is None or source.project_id != timeline.project_id or source.status != MediaStatus.READY:
            raise VideoGenerationError("A ready source asset from this project is required")
        confirmed = _tracks_document(dict(timeline.settings_json.get("confirmed_timeline", {})))
        subtitles = list(dict(timeline.settings_json.get("subtitles", {})).get("items", []))
        automatic = request.get("output_start") is None
        candidate = {"output_start": float(request["output_start"]), "duration_seconds": float(request["duration_seconds"]), "transcript": ""} if not automatic else select_broll_opportunity(confirmed, subtitles)
        if candidate is None: raise VideoGenerationError("No information-dense, visually uncovered subtitle window was found")
        source_time = timeline_source_time(confirmed, float(candidate["output_start"]))
        if source_time is None: raise VideoGenerationError("B-Roll target lies outside the confirmed timeline")
        duration = float(candidate["duration_seconds"]); aspect_ratio = str(request.get("aspect_ratio", "9:16"))
        transcript = str(candidate.get("transcript", "")) or " ".join(str(item.get("text", "")) for item in subtitles if float(item.get("start_time", 0)) < float(candidate["output_start"]) + duration and float(item.get("end_time", 0)) > float(candidate["output_start"]))
        prompt = str(request.get("prompt_override") or build_broll_prompt(transcript, aspect_ratio=aspect_ratio))
        job_id = self.request.id
        publish_project_status(str(timeline.project_id), progress=5, stage="broll_preparing", message="正在偵測高資訊密度且畫面單調的段落", job_id=job_id)
        with tempfile.TemporaryDirectory(prefix=f"broll-{timeline.id}-") as temporary:
            workdir = Path(temporary); source_video, reference, generated_raw, generated = workdir / "source.mp4", workdir / "reference.jpg", workdir / "generated-raw.mp4", workdir / "generated-broll.mp4"
            download_object(source.proxy_key or source.storage_key, str(source_video))
            motion = visual_motion_score(source_video, source_start=source_time, duration_seconds=duration)
            if automatic and motion > settings.broll_max_visual_motion:
                raise VideoGenerationError(f"The selected high-information window is visually active (motion={motion:.3f}); B-Roll is not needed")
            _run(["ffmpeg", "-y", "-ss", f"{source_time:.3f}", "-i", str(source_video), "-frames:v", "1", "-q:v", "2", str(reference)])
            reference_key = f"projects/{timeline.project_id}/generated/references/{job_id}.jpg"; upload_object(reference_key, str(reference), "image/jpeg")
            publish_project_status(str(timeline.project_id), progress=20, stage="broll_generating", message="正在呼叫影片生成模型製作 B-Roll", job_id=job_id)
            report = get_video_generation_provider(request.get("provider")).generate(prompt=prompt, duration_seconds=round(duration), aspect_ratio=aspect_ratio, output_path=generated_raw, reference_url=create_download_url(reference_key, expires_in=3600), reference_path=reference)
            publish_project_status(str(timeline.project_id), progress=82, stage="broll_packaging", message="正在修整生成片段並加入專案素材庫", job_id=job_id)
            _run(["ffmpeg", "-y", "-i", str(generated_raw), "-t", f"{duration:.3f}", "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "fast", "-movflags", "+faststart", str(generated)])
            output_key = f"projects/{timeline.project_id}/generated/broll/{job_id}.mp4"; upload_object(output_key, str(generated), "video/mp4")
            generated_size = generated.stat().st_size

        generated_asset = MediaAsset(project_id=timeline.project_id, filename=f"generated-broll-{job_id}.mp4", storage_key=output_key, media_type=MediaType.VIDEO, status=MediaStatus.READY, mime_type="video/mp4", size_bytes=generated_size, duration_seconds=duration, width=720 if aspect_ratio == "9:16" else 1280, height=1280 if aspect_ratio == "9:16" else 720, metadata_json={"generated": True, "generation_type": "b_roll", "source_asset_id": str(source.id), "prompt": prompt, "reference_key": reference_key, "visual_motion": round(motion, 4), **report})
        db.add(generated_asset); db.flush()
        broll_track = next((track for track in confirmed["tracks"] if track.get("type") == "b_roll"), None)
        if broll_track is None:
            broll_track = {"id": "generated-b-roll", "type": "b_roll", "z_index": 10, "clips": []}; confirmed["tracks"].append(broll_track)
        broll_track["clips"].append({"id": f"generated-broll-{job_id}", "source_asset_id": str(generated_asset.id), "source_start": 0, "source_end": duration, "timeline_start": round(float(candidate["output_start"]), 3), "track": "b_roll", "z_index": int(broll_track.get("z_index", 10)), "audio_enabled": False, "action": "keep", "generation": {"provider": report["provider"], "prompt": prompt, "reason": "high information density with no existing B-Roll"}})
        timeline.settings_json = {**dict(timeline.settings_json or {}), "confirmed_timeline": confirmed, "multitrack_timeline": confirmed}
        db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="broll_completed", status="completed", message="生成 B-Roll 已插入時間軸覆蓋軌", job_id=job_id)
        return {"timeline_id": timeline_id, "media_asset_id": str(generated_asset.id), "output_key": output_key, "timeline_start": candidate["output_start"], "provider": report["provider"]}
    except Exception as exc:
        db.rollback()
        if timeline is not None and is_retryable_ai_error(exc): retry_ai_task(self, exc, project_id=str(timeline.project_id), stage="broll_generation", message="影片生成服務暫時不可用", job_id=self.request.id)
        if timeline is not None: publish_project_status(str(timeline.project_id), progress=0, stage="broll_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally: db.close()


@celery_app.task(bind=True, name="video.outpaint")
def outpaint_video(self, media_asset_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None or asset.status != MediaStatus.READY: raise VideoGenerationError("A ready media asset is required for outpainting")
        duration = float(asset.duration_seconds or 0); start = float(request.get("start_time", 0)); end = float(request.get("end_time") or duration)
        if end <= start or (duration and end > duration + .01): raise VideoGenerationError("Outpainting window lies outside the source asset")
        source_width = int(asset.width or 0)
        if source_width < 2: raise VideoGenerationError("Source dimensions are required for outpainting")
        target_width = source_width if source_width % 2 == 0 else source_width - 1; target_height = int(round(target_width * 16 / 9)) // 2 * 2
        job_id = self.request.id
        publish_project_status(str(asset.project_id), progress=5, stage="outpaint_preparing", message="正在讀取畫面上下邊緣作為擴圖條件", job_id=job_id)
        with tempfile.TemporaryDirectory(prefix=f"outpaint-{asset.id}-") as temporary:
            workdir = Path(temporary); original, window, manifest, output, muxed = workdir / "source.mp4", workdir / "window.mp4", workdir / "edge-context.json", workdir / "outpainted.mp4", workdir / "outpainted-with-audio.mp4"
            download_object(asset.proxy_key if request.get("use_proxy", True) and asset.proxy_key else asset.storage_key, str(original))
            _run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(original), "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "fast", str(window)], timeout=60 * 30)
            write_edge_manifest(window, manifest, target_width=target_width, target_height=target_height)
            publish_project_status(str(asset.project_id), progress=25, stage="outpaint_generating", message="GPU 正在做時間一致性的上下畫面外擴", job_id=job_id)
            get_video_outpaint_provider().outpaint(input_path=window, output_path=output, target_width=target_width, target_height=target_height, edge_manifest=manifest)
            _run(["ffmpeg", "-y", "-i", str(output), "-i", str(window), "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(muxed)], timeout=60 * 30)
            output_key = f"projects/{asset.project_id}/derived/{asset.id}/outpaint/{job_id}.mp4"; manifest_key = f"projects/{asset.project_id}/derived/{asset.id}/outpaint/{job_id}.json"
            upload_object(output_key, str(muxed), "video/mp4"); upload_object(manifest_key, str(manifest), "application/json")
        derived = MediaAsset(project_id=asset.project_id, filename=f"outpaint-{asset.filename}.mp4", storage_key=output_key, media_type=MediaType.VIDEO, status=MediaStatus.READY, mime_type="video/mp4", duration_seconds=end - start, width=target_width, height=target_height, metadata_json={"generated": True, "generation_type": "outpaint", "parent_asset_id": str(asset.id), "source_start": start, "source_end": end, "edge_manifest_key": manifest_key, "provider": get_video_outpaint_provider().name})
        db.add(derived); db.flush()
        metadata = dict(asset.metadata_json or {}); history = list(metadata.get("outpaint_jobs", [])); history.append({"job_id": job_id, "status": "completed", "output_asset_id": str(derived.id), "output_key": output_key, "target_size": [target_width, target_height]}); metadata["outpaint_jobs"] = history[-20:]; asset.metadata_json = metadata
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="outpaint_completed", status="completed", message="已建立無黑邊的直式外擴素材", job_id=job_id)
        return {"media_asset_id": str(derived.id), "output_key": output_key, "target_width": target_width, "target_height": target_height}
    except Exception as exc:
        db.rollback()
        if asset is not None: publish_project_status(str(asset.project_id), progress=0, stage="outpaint_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally: db.close()
