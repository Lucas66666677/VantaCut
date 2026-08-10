from pathlib import Path
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset
from app.services.color_management import ACESColorPipeline, ColorManagementError
from app.services.storage import upload_bytes
from app.worker import celery_app


@celery_app.task(name="color.bake_asset_aces_lut")
def bake_asset_aces_lut(asset_id: str, camera_log: str) -> dict[str, str]:
    """Bake and persist the exact camera-Log → ACEScct LUT selected for an asset."""
    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(asset_id))
        if asset is None:
            raise ColorManagementError("Media asset not found")
        publish_project_status(str(asset.project_id), progress=20, stage="ocio_lut_baking", message="正在建立 ACES 色彩轉換 LUT")
        cube = ACESColorPipeline().bake_camera_to_acescct(camera_log)
        key = f"projects/{asset.project_id}/color/{asset.id}/camera-to-acescct.cube"
        upload_bytes(key, cube.encode("utf-8"), "application/octet-stream")
        asset.metadata_json = {
            **(asset.metadata_json or {}),
            "color_management": {"input_log": camera_log, "working_space": "ACEScct", "ocio_lut_key": key},
        }
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="ocio_lut_ready", status="completed", message="ACES 工作空間 LUT 已建立")
        return {"asset_id": asset_id, "lut_key": key, "working_space": "ACEScct"}
    except Exception as exc:
        db.rollback()
        if asset is not None:
            publish_project_status(str(asset.project_id), progress=0, stage="ocio_lut_failed", status="failed", message=str(exc))
        raise
    finally:
        db.close()
