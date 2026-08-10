"""GPU worker for depth-guided stereo synthesis and verified Apple Spatial Video packaging."""
from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import RenderJob, RenderStatus, SpatialVideoJob
from app.services.mvhevc_packaging import attach_and_verify_apple_spatial_metadata, mux_mvhevc_with_spatial_audio
from app.services.stereo_synthesis import StereoSynthesisSettings, render_stereo_pair
from app.services.storage import download_object, upload_object
from app.worker import celery_app


@celery_app.task(bind=True, name="spatial_video.render_mvhevc")
def render_mvhevc_spatial_video(self, spatial_video_job_id: str) -> dict[str, str | int]:
    db = SessionLocal()
    job: SpatialVideoJob | None = None
    try:
        job = db.get(SpatialVideoJob, UUID(spatial_video_job_id))
        if job is None:
            raise RuntimeError("Spatial video job not found")
        source = db.get(RenderJob, job.source_render_job_id)
        if source is None or source.status != RenderStatus.COMPLETED or not source.output_key:
            raise RuntimeError("Spatial Video requires a completed 7.1.4 source render")
        job.status, job.progress = "processing", 2; db.commit()
        publish_project_status(str(job.project_id), progress=2, stage="spatial_video_downloading", message="正在準備 7.1.4 空間音訊主檔", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"spatial-video-{job.id}-") as temporary:
            workdir = Path(temporary)
            source_path, left_path, right_path = workdir / "source.mov", workdir / "left.mp4", workdir / "right.mp4"
            intermediate_path, output_path = workdir / "mvhevc-intermediate.mov", workdir / "apple-spatial.mov"
            download_object(source.output_key, str(source_path))
            options = dict(job.options_json or {})
            synthesis = StereoSynthesisSettings(
                ipd_mm=float(options["ipd_mm"]), horizontal_fov_degrees=float(options["horizontal_fov_degrees"]),
                virtual_depth_range_m=float(options["virtual_depth_range_m"]), max_disparity_px=float(options["max_disparity_px"]),
                temporal_depth_smoothing=float(options["temporal_depth_smoothing"]), depth_model=str(options["depth_model"]),
            )

            def progress(value: int) -> None:
                overall = 10 + int(value * .58)
                job.progress = overall
                publish_project_status(str(job.project_id), progress=overall, stage="spatial_video_stereo", message="正在以深度估計生成左右眼視差", job_id=self.request.id)

            report = render_stereo_pair(source_path, left_path, right_path, synthesis, progress_callback=progress)
            job.progress = 72; db.commit()
            publish_project_status(str(job.project_id), progress=72, stage="spatial_video_mvhevc", message="正在以 MV-HEVC 編碼雙目視訊並保留 7.1.4 音訊", job_id=self.request.id)
            mux_report = mux_mvhevc_with_spatial_audio(left_path, right_path, source_path, intermediate_path)
            job.progress = 88; db.commit()
            metadata = {
                "format": "apple_spatial_video", "mv_hevc_video_layer_ids": [0, 1],
                "left_eye_view_id": 0, "right_eye_view_id": 1,
                "baseline_mm": synthesis.ipd_mm, "horizontal_field_of_view_degrees": synthesis.horizontal_fov_degrees,
                "disparity_adjustment": 0.0, "source": "monocular_depth_virtual_stereo",
            }
            publish_project_status(str(job.project_id), progress=88, stage="spatial_video_metadata", message="正在寫入並驗證 Apple Spatial Video metadata", job_id=self.request.id)
            verification = attach_and_verify_apple_spatial_metadata(intermediate_path, output_path, metadata, workdir)
            output_key = f"projects/{job.project_id}/exports/spatial/{job.id}/spatial-video.mov"
            upload_object(output_key, str(output_path), "video/quicktime")
        job.status, job.progress, job.output_key = "completed", 100, output_key
        job.verification_json = {"stereo_synthesis": report, "mvhevc": mux_report, "verification": verification}
        db.commit()
        publish_project_status(str(job.project_id), progress=100, stage="spatial_video_completed", status="completed", message="Apple Spatial Video 與 7.1.4 空間音訊已完成驗證", job_id=self.request.id)
        return {"spatial_video_job_id": spatial_video_job_id, "output_key": output_key, "status": "completed"}
    except Exception as exc:
        db.rollback()
        if job is not None:
            current = db.get(SpatialVideoJob, job.id)
            if current is not None:
                current.status, current.error_message = "failed", str(exc)[-4000:]
                db.commit()
                publish_project_status(str(current.project_id), progress=0, stage="spatial_video_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
