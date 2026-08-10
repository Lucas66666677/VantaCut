"""Analyse one source video and persist a render-ready stacked-layout plan."""
from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline
from app.services.storage import download_object
from app.services.vertical_dual_layout import analyze_vertical_dual_layout
from app.worker import celery_app


@celery_app.task(bind=True, name="vertical_layout.analyze")
def analyze_vertical_dual_layout_task(self, timeline_id: str, source_asset_id: str, options: dict[str, object]) -> dict[str, object]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline, asset = db.get(Timeline, UUID(timeline_id)), db.get(MediaAsset, UUID(source_asset_id))
        if timeline is None or asset is None or asset.project_id != timeline.project_id or asset.status != MediaStatus.READY or asset.media_type != MediaType.VIDEO:
            raise ValueError("Dual-screen layout source video is unavailable")
        publish_project_status(str(timeline.project_id), progress=15, stage="dual_layout_preparing", message="正在載入實況畫面", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"dual-layout-{timeline.id}-") as temporary:
            proxy = Path(temporary) / "source.mp4"; download_object(asset.proxy_key or asset.storage_key, str(proxy))
            publish_project_status(str(timeline.project_id), progress=42, stage="dual_layout_face_detection", message="正在定位鏡頭與遊戲主畫面", job_id=self.request.id)
            plan = analyze_vertical_dual_layout(proxy, top_ratio=float(options.get("top_ratio", .43)), max_samples=int(options.get("max_samples", 48)))
        settings = dict(timeline.settings_json or {})
        settings["vertical_dual_layout"] = {"status": "completed", "source_asset_id": str(asset.id), "plan": plan.to_json()}
        timeline.settings_json = settings; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="dual_layout_ready", status="completed", message="直式雙畫面已準備好，可直接導出", job_id=self.request.id, extra={"timeline_id": str(timeline.id), "face_detected": plan.face_detected})
        return {"timeline_id": str(timeline.id), "face_detected": plan.face_detected, "confidence": plan.confidence}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            settings = dict(timeline.settings_json or {}); settings["vertical_dual_layout"] = {"status": "failed", "error": str(exc)}; timeline.settings_json = settings; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="dual_layout_failed", status="failed", message="直式雙畫面分析失敗", job_id=self.request.id)
        raise
    finally:
        db.close()
