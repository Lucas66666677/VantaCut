"""GPU/CPU worker for depth-aware and dense-optical-flow transition plates."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, Timeline
from app.schemas.transitions import TransitionSpec
from app.services.storage import download_object, upload_object
from app.services.transitions import TransitionError, render_transition_asset
from app.worker import celery_app


@celery_app.task(bind=True, name="transition.build_asset")
def build_transition_asset(self, timeline_id: str, transition_id: str) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None: raise TransitionError("Timeline not found")
        graph = dict(timeline.settings_json.get("transition_graph", {})); raw = next((item for item in graph.get("transitions", []) if item.get("id") == transition_id), None)
        if raw is None: raise TransitionError("Transition specification not found")
        spec = TransitionSpec.model_validate(raw)
        if spec.kind not in {"depth_person_through", "depth_background_peel", "morph_cut"}:
            return {"transition_id": transition_id, "status": "no_pre_render_required"}
        source, target = db.get(MediaAsset, spec.source_asset_id), db.get(MediaAsset, spec.target_asset_id)
        if source is None or target is None or source.project_id != timeline.project_id or target.project_id != timeline.project_id:
            raise TransitionError("Transition assets must belong to the timeline project")
        publish_project_status(str(timeline.project_id), progress=10, stage="transition_preparing", message="正在準備轉場邊界影格", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"transition-{timeline.id}-") as temporary:
            workdir = Path(temporary); source_path, target_path, output = workdir / "source.mp4", workdir / "target.mp4", workdir / "transition.mp4"
            download_object(source.storage_key, str(source_path)); download_object(target.storage_key, str(target_path))
            publish_project_status(str(timeline.project_id), progress=35, stage="transition_rendering", message="正在計算深度／光流動態轉場", job_id=self.request.id)
            report = render_transition_asset(spec.kind, source_path, target_path, from_time=float(spec.from_source_time), to_time=float(spec.to_source_time), duration_seconds=spec.duration_seconds, output_path=output)
            key = f"projects/{timeline.project_id}/timelines/{timeline.id}/transitions/{transition_id}.mp4"; upload_object(key, str(output), "video/mp4")
        graph["transitions"] = [{**item, "render_asset_key": key, "render_report": report, "status": "completed"} if item.get("id") == transition_id else item for item in graph.get("transitions", [])]
        timeline.settings_json = {**dict(timeline.settings_json or {}), "transition_graph": graph}; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="transition_completed", status="completed", message="動態轉場素材已生成", job_id=self.request.id)
        return {"transition_id": transition_id, "render_asset_key": key, "report": report}
    except Exception as exc:
        db.rollback()
        if timeline is not None: publish_project_status(str(timeline.project_id), progress=0, stage="transition_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally: db.close()
