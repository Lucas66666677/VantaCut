"""GPU-capable background task for generating reusable 2.5D parallax layers."""
from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset
from app.services.parallax_zoom import generate_parallax_layers
from app.services.storage import download_object, upload_object
from app.worker import celery_app


@celery_app.task(bind=True, name="parallax.generate_layers")
def generate_layers(self, media_asset_id: str, depth_model: str = "auto", use_proxy: bool = True) -> dict[str, str | int | float]:
    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None:
            raise RuntimeError("Media asset not found")
        source_key = asset.proxy_key if use_proxy and asset.proxy_key else asset.storage_key
        publish_project_status(str(asset.project_id), progress=5, stage="parallax_downloading", message="正在準備深度視差圖層", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"parallax-{asset.id}-") as temporary:
            workdir = Path(temporary)
            source = workdir / "source.mp4"
            download_object(source_key, str(source))
            publish_project_status(str(asset.project_id), progress=18, stage="parallax_depth", message="正在以單目深度拆分前景與背景", job_id=self.request.id)
            layers = generate_parallax_layers(source, workdir / "layers", depth_model=depth_model)
            base = f"projects/{asset.project_id}/derived/{asset.id}/parallax/{self.request.id}"
            background_key, foreground_key = f"{base}/background.mp4", f"{base}/foreground-alpha.webm"
            publish_project_status(str(asset.project_id), progress=88, stage="parallax_uploading", message="正在上傳 2.5D 視差圖層", job_id=self.request.id)
            upload_object(background_key, str(layers.background_video), "video/mp4")
            upload_object(foreground_key, str(layers.foreground_alpha_video), "video/webm")
        metadata = dict(asset.metadata_json or {})
        metadata["parallax_layers"] = {"status": "completed", "background_key": background_key, "foreground_alpha_key": foreground_key, "fps": layers.fps, "width": layers.width, "height": layers.height, "frame_count": layers.frame_count, "source": "proxy" if source_key == asset.proxy_key else "original"}
        asset.metadata_json = metadata
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="parallax_completed", status="completed", message="2.5D 視差圖層已完成", job_id=self.request.id)
        return {"background_key": background_key, "foreground_alpha_key": foreground_key, "frame_count": layers.frame_count, "fps": layers.fps}
    except Exception as exc:
        db.rollback()
        if asset is not None:
            current = db.get(MediaAsset, asset.id)
            if current is not None:
                current.metadata_json = {**dict(current.metadata_json or {}), "parallax_layers": {"status": "failed", "error": str(exc)}}
                db.commit()
            publish_project_status(str(asset.project_id), progress=0, stage="parallax_failed", status="failed", message="2.5D 視差圖層生成失敗，請重試", job_id=self.request.id)
        raise
    finally:
        db.close()
