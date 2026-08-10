from __future__ import annotations
import tempfile
from pathlib import Path
from uuid import UUID
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset
from app.services.spatial_text import solve_spatial_tracking
from app.services.storage import download_object, upload_object
from app.worker import celery_app

@celery_app.task(bind=True, name="spatial_text.analyze")
def analyze_spatial_text(self, media_asset_id: str, use_proxy: bool = True) -> dict[str, object]:
    db = SessionLocal(); asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None: raise ValueError("Media asset not found")
        publish_project_status(str(asset.project_id), progress=10, stage="spatial_text_depth", message="正在估算逐幀深度與前景遮擋", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"spatial-text-{asset.id}-") as temporary:
            workdir = Path(temporary); source = workdir / "source.mp4"; download_object(asset.proxy_key if use_proxy and asset.proxy_key else asset.storage_key, str(source))
            depth, poses, frames, fps = solve_spatial_tracking(source, workdir)
            base = f"projects/{asset.project_id}/derived/{asset.id}/spatial-text/{self.request.id}"; depth_key, poses_key = f"{base}/depth.mp4", f"{base}/camera-poses.json"
            upload_object(depth_key, str(depth), "video/mp4"); upload_object(poses_key, str(poses), "application/json")
        asset.metadata_json = {**dict(asset.metadata_json or {}), "spatial_text_tracking": {"status": "completed", "depth_key": depth_key, "camera_poses_key": poses_key, "frame_count": frames, "fps": fps}}
        db.commit(); publish_project_status(str(asset.project_id), progress=100, stage="spatial_text_ready", status="completed", message="3D 文字深度與相機軌跡已準備完成", job_id=self.request.id)
        return {"depth_key": depth_key, "camera_poses_key": poses_key}
    except Exception as exc:
        db.rollback(); raise
    finally: db.close()
