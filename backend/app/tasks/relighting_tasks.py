from __future__ import annotations

import tempfile
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset
from app.services.storage import download_object, upload_object
from app.services.virtual_relighting import RelativeDepthEstimator, analyze_video_depth
from app.worker import celery_app


@celery_app.task(bind=True, name="relighting.analyze_depth_and_lighting")
def analyze_depth_and_lighting(
    self, media_asset_id: str, depth_model: str = "auto", frame_stride: int = 1, use_proxy: bool = False,
) -> dict[str, Any]:
    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None:
            raise RuntimeError("Media asset not found")
        source_key = asset.proxy_key if use_proxy and asset.proxy_key else asset.storage_key
        base = f"projects/{asset.project_id}/derived/{asset.id}/relighting"
        publish_project_status(str(asset.project_id), progress=5, stage="relight_downloading", message="正在準備高解析度影片深度分析", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"relight-{asset.id}-") as temporary:
            workdir = Path(temporary)
            source = workdir / "source.mp4"
            manifest_path = workdir / "depth-frame-manifest.jsonl"
            download_object(source_key, str(source))
            estimator = RelativeDepthEstimator(depth_model)

            def persist_frame(timestamp: float, depth: Any, normals: Any, key_light: dict[str, Any]) -> None:
                import cv2
                import numpy as np
                identifier = f"{int(round(timestamp * 1000)):010d}"
                depth_path, normals_path = workdir / f"depth-{identifier}.png", workdir / f"normals-{identifier}.png"
                cv2.imwrite(str(depth_path), np.clip(depth * 65535, 0, 65535).astype(np.uint16))
                normal_rgb = np.clip((normals + 1) * .5 * 255, 0, 255).astype(np.uint8)
                cv2.imwrite(str(normals_path), normal_rgb[..., ::-1])
                depth_key, normals_key = f"{base}/depth/{identifier}.png", f"{base}/normals/{identifier}.png"
                upload_object(depth_key, str(depth_path), "image/png")
                upload_object(normals_key, str(normals_path), "image/png")
                with manifest_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"source_time": round(timestamp, 3), "depth_key": depth_key, "normals_key": normals_key, "key_light": key_light}, ensure_ascii=False) + "\n")

            publish_project_status(str(asset.project_id), progress=20, stage="relight_depth", message="正在逐幀估測相對深度與法線", job_id=self.request.id)
            report = analyze_video_depth(source, estimator=estimator, frame_stride=frame_stride, on_frame=persist_frame)
            manifest_key = f"{base}/depth-frame-manifest.jsonl"
            upload_object(manifest_key, str(manifest_path), "application/x-ndjson")
        report.update({"status": "completed", "source": "proxy" if source_key == asset.proxy_key else "original", "frame_manifest_key": manifest_key})
        asset.metadata_json = {**dict(asset.metadata_json or {}), "relighting_analysis": report}
        db.add(AIAnalysis(media_asset_id=asset.id, analysis_type=AnalysisType.RELIGHTING, model_name=report["depth_model"], status="completed", result_json=report, confidence=float(report["key_light_confidence"])) )
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="relight_completed", status="completed", message=f"深度、法線與光源分析完成（{report['processed_frames']} 幀）", job_id=self.request.id)
        return {"media_asset_id": media_asset_id, "processed_frames": report["processed_frames"], "key_light_confidence": report["key_light_confidence"]}
    except Exception as exc:
        db.rollback()
        if asset is not None:
            current = db.get(MediaAsset, asset.id)
            if current is not None:
                current.metadata_json = {**dict(current.metadata_json or {}), "relighting_analysis": {"status": "failed", "error": str(exc)}}
                db.commit()
            publish_project_status(str(asset.project_id), progress=0, stage="relight_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
