from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import Timeline
from app.services.data_charts import render_lottie_or_reference, write_lottie_bundle
from app.services.storage import upload_object
from app.worker import celery_app


@celery_app.task(bind=True, name="data_charts.generate_chart")
def generate_chart(self, timeline_id: str, chart_id: str) -> dict[str, Any]:
    db = SessionLocal()
    timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise RuntimeError("Timeline not found")
        settings = copy.deepcopy(dict(timeline.settings_json or {}))
        charts = list(settings.get("data_chart_overlays", []))
        chart_index = next((index for index, item in enumerate(charts) if item.get("id") == chart_id), None)
        if chart_index is None:
            raise RuntimeError("Data chart request not found")
        chart = dict(charts[chart_index])
        publish_project_status(str(timeline.project_id), progress=20, stage="chart_lottie", message="正在生成向量走勢動畫", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"chart-{chart_id}-") as temporary:
            workdir = Path(temporary)
            lottie_json, lottie_bundle, rgba_movie = workdir / "chart.json", workdir / "chart.lottie", workdir / "chart-alpha.mov"
            write_lottie_bundle(chart, lottie_json, lottie_bundle)
            publish_project_status(str(timeline.project_id), progress=55, stage="chart_rgba_render", message="正在生成無損透明圖表影格", job_id=self.request.id)
            renderer = render_lottie_or_reference(chart, lottie_json, rgba_movie)
            base = f"projects/{timeline.project_id}/timelines/{timeline.id}/data-charts/{chart_id}"
            json_key, lottie_key, rgba_key = f"{base}/chart.json", f"{base}/chart.lottie", f"{base}/chart-alpha.mov"
            upload_object(json_key, str(lottie_json), "application/json")
            upload_object(lottie_key, str(lottie_bundle), "application/zip")
            upload_object(rgba_key, str(rgba_movie), "video/quicktime")
        charts[chart_index] = {**chart, "status": "completed", "lottie_json_key": json_key, "lottie_key": lottie_key, "rgba_video_key": rgba_key, "renderer": renderer, "encoding": "qtrle/argb" if renderer == "opencv_rgba_reference" else "renderer_managed_alpha"}
        settings["data_chart_overlays"] = charts
        timeline.settings_json = settings
        db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="chart_completed", status="completed", message="動態圖表已可加入時間軸", job_id=self.request.id)
        return {"chart_id": chart_id, "lottie_key": lottie_key, "rgba_video_key": rgba_key}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                settings = copy.deepcopy(dict(current.settings_json or {}))
                settings["data_chart_overlays"] = [{**item, "status": "failed", "error": str(exc)} if item.get("id") == chart_id else item for item in settings.get("data_chart_overlays", [])]
                current.settings_json = settings
                db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="chart_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
