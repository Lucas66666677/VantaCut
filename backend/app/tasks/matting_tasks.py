"""GPU task for SAM 2 click/text prompted video mattes and alpha artifacts."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus
from app.services.storage import download_object, upload_object
from app.services.video_matting import (
    SAM2VideoMattingProvider, VideoMattingError, reference_frame, refine_matte_sequence,
    render_alpha_webm, save_matte_manifest,
)
from app.worker import celery_app


def _fps_and_reference_index(video_path: Path, frame_time: float) -> int:
    import cv2

    capture = cv2.VideoCapture(str(video_path))
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        capture.release()
    return min(max(0, round(frame_time * fps)), max(0, count - 1))


@celery_app.task(bind=True, name="matting.generate_video_matte")
def generate_video_matte(self, media_asset_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Create alpha masks, despilled RGBA preview, and a manifest usable by Timeline effects."""
    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None or asset.status != MediaStatus.READY:
            raise VideoMattingError("A ready media asset is required for matting")
        source_key = asset.proxy_key if request.get("use_proxy", True) and asset.proxy_key else asset.storage_key
        job_id = self.request.id
        base = f"projects/{asset.project_id}/derived/{asset.id}/matting/{job_id}"

        def report(progress: float, stage: str) -> None:
            publish_project_status(
                str(asset.project_id), progress=int(12 + progress * 75), stage=stage,
                message="SAM 2 正在追蹤物件並細化髮絲邊緣", job_id=job_id,
            )

        publish_project_status(str(asset.project_id), progress=5, stage="matting_downloading", message="正在準備影片與分割模型", job_id=job_id)
        with tempfile.TemporaryDirectory(prefix=f"matting-{asset.id}-") as temporary:
            workdir = Path(temporary)
            source, raw_masks, refined = workdir / "source.mp4", workdir / "sam2-masks", workdir / "refined"
            manifest, alpha_webm = workdir / "matte-manifest.json", workdir / "matte-alpha.webm"
            download_object(source_key, str(source))
            reference_index = _fps_and_reference_index(source, float(request.get("frame_time", 0)))
            provider = SAM2VideoMattingProvider()
            publish_project_status(str(asset.project_id), progress=15, stage="matting_segmenting", message="SAM 2 正在建立初始物件遮罩", job_id=job_id)
            if request["mode"] == "click":
                masks = provider.track_from_clicks(
                    source, frame_index=reference_index, points=list(request["points"]), output_dir=raw_masks, progress=report,
                )
            else:
                initial_mask = provider.text_prompt_mask(reference_frame(source, reference_index), str(request["text_prompt"]), workdir / "clip-proposals")
                masks = provider.track_from_initial_mask(
                    source, frame_index=reference_index, initial_mask=initial_mask, output_dir=raw_masks, progress=report,
                )
            publish_project_status(str(asset.project_id), progress=78, stage="matting_refining", message="正在羽化邊緣、去溢色並穩定時間軸", job_id=job_id)
            frames, fps = refine_matte_sequence(
                source, masks, output_dir=refined, feather_pixels=float(request.get("feather_pixels", 2.5)),
                despill_strength=float(request.get("despill_strength", .65)), progress=report,
            )
            save_matte_manifest(manifest, frames, provider=provider.name, mode=str(request["mode"]), source_fps=fps)
            render_alpha_webm(refined / "rgba", fps, alpha_webm)
            publish_project_status(str(asset.project_id), progress=92, stage="matting_uploading", message="正在上傳 Alpha 遮罩與預覽圖層", job_id=job_id)
            alpha_prefix = f"{base}/alpha"
            for frame in frames:
                upload_object(f"{alpha_prefix}/{Path(frame.alpha_path).name}", frame.alpha_path, "image/png")
            manifest_key, alpha_webm_key = f"{base}/matte-manifest.json", f"{base}/matte-alpha.webm"
            upload_object(manifest_key, str(manifest), "application/json")
            upload_object(alpha_webm_key, str(alpha_webm), "video/webm")

        metadata = dict(asset.metadata_json or {})
        history = list(metadata.get("matting_jobs", []))
        history.append({
            "job_id": job_id, "status": "completed", "provider": "sam2", "mode": request["mode"],
            "alpha_prefix": alpha_prefix, "alpha_webm_key": alpha_webm_key, "manifest_key": manifest_key,
            "frame_count": len(frames), "source": "proxy" if source_key == asset.proxy_key else "original",
        })
        metadata["matting_jobs"] = history[-20:]
        asset.metadata_json = metadata
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="matting_completed", status="completed", message="智慧摳像 Alpha 遮罩已準備完成", job_id=job_id)
        return {"media_asset_id": media_asset_id, "manifest_key": manifest_key, "alpha_webm_key": alpha_webm_key, "frame_count": len(frames)}
    except Exception as exc:
        db.rollback()
        if asset is not None:
            current = db.get(MediaAsset, asset.id)
            if current is not None:
                metadata = dict(current.metadata_json or {})
                history = list(metadata.get("matting_jobs", []))
                history.append({"job_id": self.request.id, "status": "failed", "error": str(exc)})
                metadata["matting_jobs"] = history[-20:]
                current.metadata_json = metadata
                db.commit()
            publish_project_status(str(asset.project_id), progress=0, stage="matting_failed", status="failed", message="智慧摳像失敗，請重試", job_id=self.request.id)
        raise
    finally:
        db.close()
