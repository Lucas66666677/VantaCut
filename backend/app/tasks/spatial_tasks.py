"""GPU tasks for video-to-COLMAP-to-3DGS reconstruction and novel virtual-camera renders."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus
from app.services.spatial_reconstruction import SpatialReconstructionError, reconstruct_scene, render_virtual_camera
from app.services.storage import download_object, upload_object
from app.worker import celery_app


@celery_app.task(bind=True, name="spatial.reconstruct_scene")
def reconstruct_spatial_scene(self, media_asset_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None or asset.status != MediaStatus.READY: raise SpatialReconstructionError("A ready video asset is required")
        source_key = asset.proxy_key if request.get("use_proxy") and asset.proxy_key else asset.storage_key; scene_id = self.request.id
        publish_project_status(str(asset.project_id), progress=3, stage="spatial_downloading", message="正在準備環繞拍攝影片", job_id=scene_id)
        with tempfile.TemporaryDirectory(prefix=f"spatial-{asset.id}-") as temporary:
            workdir = Path(temporary); source = workdir / "source.mp4"; download_object(source_key, str(source))
            def progress(value: float, stage: str) -> None:
                labels = {"spatial_frames_extracted": "正在抽取可重建影格", "spatial_features": "正在提取 COLMAP 特徵", "spatial_matching": "正在匹配連續視角", "spatial_poses": "正在解算攝影機姿態", "spatial_dense_depth": "正在融合密集點雲", "spatial_3dgs_training": "GPU 正在訓練 3D Gaussian Splats", "spatial_3dgs_trained": "3D Gaussian 場景已收斂"}
                publish_project_status(str(asset.project_id), progress=int(5 + value * 88), stage=stage, message=labels.get(stage, "正在建構空間場景"), job_id=scene_id)
            result = reconstruct_scene(source, workdir, frame_rate=float(request["frame_rate"]), max_frames=int(request["max_frames"]), iterations=int(request["iterations"]), progress=progress)
            base = f"projects/{asset.project_id}/spatial-scenes/{scene_id}"
            dense_key, splat_key, poses_key = f"{base}/dense-point-cloud.ply", f"{base}/scene-3dgs.ply", f"{base}/camera-poses.json"
            upload_object(dense_key, str(result.dense_point_cloud), "application/octet-stream"); upload_object(splat_key, str(result.splat_ply), "application/octet-stream"); upload_object(poses_key, str(result.poses_json), "application/json")
        scene = {"scene_id": scene_id, "status": "completed", "source_asset_id": str(asset.id), "dense_point_cloud_key": dense_key, "splat_ply_key": splat_key, "camera_poses_key": poses_key, "frame_count": result.frame_count, "registered_pose_count": result.registered_pose_count, "coordinate_system": "COLMAP camera-to-world", "virtual_camera_renders": []}
        asset.metadata_json = {**dict(asset.metadata_json or {}), "spatial_scene": scene}; db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="spatial_completed", status="completed", message="3D Gaussian Splat 場景與攝影機姿態已建立", job_id=scene_id)
        return scene
    except Exception as exc:
        db.rollback()
        if asset is not None:
            current = db.get(MediaAsset, asset.id)
            if current is not None: current.metadata_json = {**dict(current.metadata_json or {}), "spatial_scene": {"status": "failed", "error": str(exc)}}; db.commit()
            publish_project_status(str(asset.project_id), progress=0, stage="spatial_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally: db.close()


@celery_app.task(bind=True, name="spatial.render_virtual_camera")
def render_spatial_virtual_camera(self, media_asset_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None: raise SpatialReconstructionError("Media asset not found")
        scene = dict((asset.metadata_json or {}).get("spatial_scene", {}))
        if scene.get("status") != "completed" or not scene.get("splat_ply_key"): raise SpatialReconstructionError("A completed spatial scene is required")
        job_id = self.request.id; publish_project_status(str(asset.project_id), progress=10, stage="spatial_virtual_camera_preparing", message="正在載入 3D Gaussian 場景與虛擬鏡頭路徑", job_id=job_id)
        with tempfile.TemporaryDirectory(prefix=f"spatial-render-{asset.id}-") as temporary:
            workdir = Path(temporary); splat, output = workdir / "scene.ply", workdir / "virtual-camera.mp4"; download_object(str(scene["splat_ply_key"]), str(splat))
            publish_project_status(str(asset.project_id), progress=35, stage="spatial_virtual_camera_rendering", message="GPU 正在從新視角重新投影 Gaussian Splats", job_id=job_id)
            render_virtual_camera(splat_path=splat, camera_path=list(request["camera_path"]), output_path=output, fps=int(request["fps"]), width=int(request["width"]), height=int(request["height"]))
            output_key = f"projects/{asset.project_id}/spatial-scenes/{scene['scene_id']}/virtual-renders/{job_id}.mp4"; upload_object(output_key, str(output), "video/mp4")
        renders = list(scene.get("virtual_camera_renders", [])); renders.append({"job_id": job_id, "status": "completed", "output_key": output_key, "camera_path": request["camera_path"], "fps": request["fps"], "width": request["width"], "height": request["height"]}); scene["virtual_camera_renders"] = renders[-20:]; asset.metadata_json = {**dict(asset.metadata_json or {}), "spatial_scene": scene}; db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="spatial_virtual_camera_completed", status="completed", message="新運鏡的 3D 場景影片已導出", job_id=job_id)
        return {"output_key": output_key, "scene_id": scene["scene_id"]}
    except Exception as exc:
        db.rollback()
        if asset is not None: publish_project_status(str(asset.project_id), progress=0, stage="spatial_virtual_camera_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally: db.close()
