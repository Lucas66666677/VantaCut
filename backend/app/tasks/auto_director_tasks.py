from __future__ import annotations

from typing import Any
from uuid import UUID

from app.autodirector.pipeline import DirectorAgent
from app.core.ai_retry import retry_ai_task
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AutoDirectorRun, AutoDirectorStatus
from app.worker import celery_app


@celery_app.task(bind=True, name="director.create_documentary")
def create_documentary(self, run_id: str) -> dict[str, Any]:
    """Main loop for the unattended Director Agent workflow."""
    db = SessionLocal()
    run: AutoDirectorRun | None = None
    try:
        run = db.get(AutoDirectorRun, UUID(run_id))
        if run is None:
            raise ValueError("Auto Director run not found")

        def progress(percent: int, stage: str, message: str) -> None:
            publish_project_status(str(run.project_id), progress=percent, stage=stage, message=message, job_id=self.request.id)

        timeline = DirectorAgent().run(db, run=run, progress=progress)
        return {"run_id": run_id, "timeline_id": str(timeline.id), "status": AutoDirectorStatus.READY_FOR_REVIEW.value}
    except Exception as exc:
        db.rollback()
        if run is not None and retry_ai_task(
            self, exc, project_id=str(run.project_id), stage="director_retrying",
            message="自動導演服務暫時不可用，正在重試", job_id=self.request.id,
        ):
            raise AssertionError("retry_ai_task either raises or returns False")
        if run is not None:
            current = db.get(AutoDirectorRun, run.id)
            if current is not None:
                current.status = AutoDirectorStatus.FAILED
                current.error_message = str(exc)
                db.commit()
            publish_project_status(
                str(run.project_id), progress=0, stage="director_failed", status="failed",
                message="自動導演未能完成，請重試", job_id=self.request.id,
            )
        raise
    finally:
        db.close()
