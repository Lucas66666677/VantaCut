from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus
from app.services.optical_flow import retime_video_with_flow
from app.services.optical_effects import OpticalEffectSettings, apply_optical_effects
from app.services.optical_metadata import MidasDepthEstimator, estimate_optics_from_depth, extract_optical_metadata
from app.services.storage import download_object, upload_object
from app.worker import celery_app


def _sample_frame(path: Path) -> Any:
    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError("Unable to decode source video for optical estimation")
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_count // 10))
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError("Unable to read source frame for optical estimation")
    return frame


def _atempo_filter(slow_motion_factor: float) -> str:
    tempo = 1.0 / slow_motion_factor
    filters: list[str] = []
    while tempo < 0.5:
        filters.append("atempo=0.5")
        tempo /= 0.5
    while tempo > 2.0:
        filters.append("atempo=2.0")
        tempo /= 2.0
    filters.append(f"atempo={tempo:.8f}")
    return ",".join(filters)


@celery_app.task(name="media.analyze_optics")
def analyze_optics(media_asset_id: str) -> dict[str, Any]:
    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None:
            raise RuntimeError("Media asset not found")
        with tempfile.TemporaryDirectory(prefix=f"optics-{asset.id}-") as temporary:
            source = Path(temporary) / "source"
            download_object(asset.storage_key, str(source))
            optics = extract_optical_metadata(source)
            if optics.get("horizontal_fov_degrees") is None:
                publish_project_status(str(asset.project_id), progress=90, stage="optics_depth_estimation", message="缺少鏡頭資料，正在以單目深度估測", job_id=None)
                try:
                    frame = _sample_frame(source)
                    depth = MidasDepthEstimator().estimate(frame)
                    optics["estimated"] = estimate_optics_from_depth(frame, depth)
                except Exception as estimate_error:
                    optics["estimated"] = {
                        "source": "unavailable", "confidence": "none", "error": str(estimate_error),
                        "limitations": "No metadata and AI depth estimation was unavailable.",
                    }
        asset.metadata_json = {**dict(asset.metadata_json or {}), "optics": optics}
        db.commit()
        return {"media_asset_id": media_asset_id, "optics": optics}
    finally:
        db.close()


@celery_app.task(bind=True, name="video.retime_optical_flow")
def retime_with_optical_flow(
    self, media_asset_id: str, slow_motion_factor: float, apply_motion_blur: bool = False, use_proxy: bool = True,
) -> dict[str, Any]:
    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None or asset.status != MediaStatus.READY:
            raise RuntimeError("Media asset must be ready before optical-flow retiming")
        source_key = asset.proxy_key if use_proxy and asset.proxy_key else asset.storage_key
        publish_project_status(str(asset.project_id), progress=5, stage="flow_downloading", message="正在準備光流素材", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"flow-{asset.id}-") as temporary:
            workdir = Path(temporary)
            source = workdir / "source.mp4"
            silent = workdir / "retimed-silent.mp4"
            output = workdir / "retimed.mp4"
            download_object(source_key, str(source))
            publish_project_status(str(asset.project_id), progress=20, stage="flow_estimating", message="正在計算每個像素的運動向量", job_id=self.request.id)
            report = retime_video_with_flow(source, silent, slow_motion_factor=slow_motion_factor, apply_motion_blur=apply_motion_blur)
            publish_project_status(str(asset.project_id), progress=85, stage="flow_muxing", message="正在重定時並同步音訊", job_id=self.request.id)
            subprocess.run([
                "ffmpeg", "-y", "-i", str(silent), "-i", str(source), "-map", "0:v:0", "-map", "1:a?",
                "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-filter:a", _atempo_filter(slow_motion_factor),
                "-shortest", "-movflags", "+faststart", str(output),
            ], check=True, capture_output=True, text=True, timeout=2 * 60 * 60)
            key = f"projects/{asset.project_id}/derived/{asset.id}/flow-slowmo-{slow_motion_factor:.2f}x.mp4"
            upload_object(key, str(output), "video/mp4")
        asset.metadata_json = {
            **dict(asset.metadata_json or {}),
            "optical_flow": {
                "status": "completed", "algorithm": "farneback_dense", "slow_motion_factor": slow_motion_factor,
                "motion_blur_compensation": apply_motion_blur, "source": "proxy" if source_key == asset.proxy_key else "original",
                "output_key": key, **report,
            },
        }
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="flow_completed", status="completed", message="光流慢動作完成", job_id=self.request.id)
        return {"media_asset_id": media_asset_id, "output_key": key, **report}
    except Exception as exc:
        db.rollback()
        if asset is not None:
            current = db.get(MediaAsset, asset.id)
            if current is not None:
                current.metadata_json = {**dict(current.metadata_json or {}), "optical_flow": {"status": "failed", "error": str(exc)}}
                db.commit()
            publish_project_status(str(asset.project_id), progress=0, stage="flow_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="video.render_optical_look_preview")
def render_optical_look_preview(self, media_asset_id: str, effect_settings: dict[str, Any]) -> dict[str, str]:
    """Render a proxy look with chromatic aberration, cos^4 vignetting, and depth-aware bokeh."""
    import cv2

    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None or asset.status != MediaStatus.READY or not asset.proxy_key:
            raise RuntimeError("A ready media asset with proxy video is required")
        settings = OpticalEffectSettings(**effect_settings)
        with tempfile.TemporaryDirectory(prefix=f"optical-look-{asset.id}-") as temporary:
            workdir = Path(temporary)
            source, silent, output = workdir / "proxy.mp4", workdir / "look-silent.mp4", workdir / "look.mp4"
            download_object(asset.proxy_key, str(source))
            capture = cv2.VideoCapture(str(source))
            fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
            width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
            estimator = MidasDepthEstimator() if settings.bokeh_radius_px > 0 else None
            depth = None
            frame_index = 0
            try:
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    if estimator is not None and (depth is None or frame_index % 4 == 0):
                        depth = estimator.estimate(frame)
                    writer.write(apply_optical_effects(frame, settings, relative_depth=depth))
                    frame_index += 1
            finally:
                capture.release()
                writer.release()
            subprocess.run([
                "ffmpeg", "-y", "-i", str(silent), "-i", str(source), "-map", "0:v:0", "-map", "1:a?",
                "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(output),
            ], check=True, capture_output=True, text=True, timeout=2 * 60 * 60)
            key = f"projects/{asset.project_id}/derived/{asset.id}/optical-look-preview.mp4"
            upload_object(key, str(output), "video/mp4")
        asset.metadata_json = {**dict(asset.metadata_json or {}), "optical_look_preview": {"status": "completed", "key": key, "settings": effect_settings}}
        db.commit()
        return {"media_asset_id": media_asset_id, "output_key": key}
    finally:
        db.close()
