from uuid import UUID

from app.models.entities import MediaAsset, MediaStatus
from app.db.session import SessionLocal
from app.worker import celery_app


@celery_app.task(name="media.preprocess")
def preprocess_media(asset_id: str) -> None:
    db = SessionLocal()
    try:
        asset = db.get(MediaAsset, UUID(asset_id))
        if asset is None:
            return
        # TODO: ffprobe metadata, proxy generation, thumbnail extraction and audio extraction.
        asset.status = MediaStatus.READY
        db.commit()
    except Exception:
        asset = db.get(MediaAsset, UUID(asset_id))
        if asset is not None:
            asset.status = MediaStatus.FAILED
            db.commit()
        raise
    finally:
        db.close()

