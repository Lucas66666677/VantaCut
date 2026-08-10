"""GPU-oriented background job for short-window object removal and temporal inpainting."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus
from app.services.storage import download_object, upload_object
from app.services.video_inpainting import (
    NormalizedBox,
    VideoInpaintingError,
    get_video_inpainting_provider,
    save_tracking_manifest,
    track_mask_window,
)
from app.worker import celery_app


def _run(command: list[str], *, timeout: int = 2 * 60 * 60) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise VideoInpaintingError("FFmpeg operation timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise VideoInpaintingError((exc.stderr or "FFmpeg operation failed")[-2000:]) from exc


def _box_from_brush_strokes(strokes: list[dict[str, Any]]) -> NormalizedBox:
    points = [point for stroke in strokes for point in list(stroke.get("points", []))]
    if not points:
        raise VideoInpaintingError("No brush points were supplied")
    radius = max(float(stroke.get("radius", .035)) for stroke in strokes)
    left = max(0.0, min(float(point["x"]) for point in points) - radius)
    top = max(0.0, min(float(point["y"]) for point in points) - radius)
    right = min(1.0, max(float(point["x"]) for point in points) + radius)
    bottom = min(1.0, max(float(point["y"]) for point in points) + radius)
    return NormalizedBox(left, top, max(.001, right - left), max(.001, bottom - top))


@celery_app.task(bind=True, name="video.inpaint_selected_object")
def inpaint_selected_object(self, media_asset_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Create a repaired preview clip and a mask-trajectory manifest for later timeline replacement."""
    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None or asset.status != MediaStatus.READY:
            raise VideoInpaintingError("A ready media asset is required")
        reference_time = float(request["frame_time"])
        before = float(request.get("before_seconds", 3))
        after = float(request.get("after_seconds", 3))
        duration = float(asset.duration_seconds or 0)
        source_start = max(0.0, float(request.get("start_time", reference_time - before)))
        requested_end = float(request.get("end_time", reference_time + after))
        source_end = min(duration, requested_end) if duration else requested_end
        if source_end - source_start < 0.1:
            raise VideoInpaintingError("The requested inpainting window is too short")
        source_key = asset.proxy_key if bool(request.get("use_proxy", True)) and asset.proxy_key else asset.storage_key
        job_id = self.request.id
        publish_project_status(str(asset.project_id), progress=3, stage="inpaint_downloading", message="正在準備修復素材", job_id=job_id)

        with tempfile.TemporaryDirectory(prefix=f"inpaint-{asset.id}-") as temporary:
            workdir = Path(temporary)
            source, context, repaired_silent, repaired, masks, manifest = (
                workdir / "source.mp4", workdir / "context.mp4", workdir / "repaired-silent.mp4", workdir / "repaired-preview.mp4", workdir / "masks", workdir / "trajectory.json"
            )
            download_object(source_key, str(source))
            publish_project_status(str(asset.project_id), progress=10, stage="inpaint_context", message="正在擷取前後畫面脈絡", job_id=job_id)
            _run([
                "ffmpeg", "-y", "-ss", f"{source_start:.4f}", "-t", f"{source_end - source_start:.4f}", "-i", str(source),
                "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart", str(context),
            ])

            def tracking_progress(value: float, stage: str) -> None:
                publish_project_status(str(asset.project_id), progress=int(15 + value * 30), stage=stage, message="正在追蹤遮罩的時空軌跡", job_id=job_id)

            strokes = list(request.get("mask_strokes", []))
            initial_box = NormalizedBox(**dict(request["mask_box"])) if request.get("mask_box") else _box_from_brush_strokes(strokes)
            tracked = track_mask_window(
                context,
                reference_time=reference_time - source_start,
                initial_box=initial_box,
                initial_strokes=strokes or None,
                output_dir=masks,
                progress=tracking_progress,
            )
            save_tracking_manifest(manifest, tracked, source_offset=source_start)
            publish_project_status(str(asset.project_id), progress=48, stage="inpaint_masks_ready", message="遮罩追蹤完成，正在進行生成式背景補幀", job_id=job_id)

            def inference_progress(value: float, stage: str) -> None:
                publish_project_status(str(asset.project_id), progress=int(50 + value * 37), stage=stage, message="ProPainter 正在進行時空一致性修復", job_id=job_id)

            get_video_inpainting_provider().inpaint(video_path=context, masks_dir=masks, output_path=repaired_silent, progress=inference_progress)
            publish_project_status(str(asset.project_id), progress=90, stage="inpaint_muxing", message="正在同步原始音訊並封裝預覽", job_id=job_id)
            _run([
                "ffmpeg", "-y", "-i", str(repaired_silent), "-i", str(context), "-map", "0:v:0", "-map", "1:a?",
                "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(repaired),
            ])
            base = f"projects/{asset.project_id}/derived/{asset.id}/inpainting/{job_id}"
            output_key, trajectory_key = f"{base}/repaired-preview.mp4", f"{base}/mask-trajectory.json"
            upload_object(output_key, str(repaired), "video/mp4")
            upload_object(trajectory_key, str(manifest), "application/json")

        metadata = dict(asset.metadata_json or {})
        history = list(metadata.get("video_inpainting_jobs", []))
        history.append({
            "job_id": job_id, "status": "completed", "provider": get_video_inpainting_provider().name,
            "source_start": round(source_start, 4), "source_end": round(source_end, 4),
            "output_key": output_key, "trajectory_key": trajectory_key, "mask_frame_count": len(tracked),
        })
        metadata["video_inpainting_jobs"] = history[-20:]
        asset.metadata_json = metadata
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="inpaint_completed", status="completed", message="動態修復預覽完成", job_id=job_id)
        return {"media_asset_id": media_asset_id, "output_key": output_key, "trajectory_key": trajectory_key, "source_start": source_start, "source_end": source_end}
    except Exception as exc:
        db.rollback()
        if asset is not None:
            current = db.get(MediaAsset, asset.id)
            if current is not None:
                metadata = dict(current.metadata_json or {})
                history = list(metadata.get("video_inpainting_jobs", []))
                history.append({"job_id": self.request.id, "status": "failed", "error": str(exc)})
                metadata["video_inpainting_jobs"] = history[-20:]
                current.metadata_json = metadata
                db.commit()
            publish_project_status(str(asset.project_id), progress=0, stage="inpaint_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
