import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import Timeline, User
from app.schemas.data_charts import DataChartRequest, DataChartTaskResponse
from app.tasks.data_chart_tasks import generate_chart


router = APIRouter(prefix="/timelines", tags=["data-charts"])


@router.post("/{timeline_id}/data-charts", response_model=DataChartTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def create_data_chart(timeline_id: UUID, payload: DataChartRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DataChartTaskResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot create a chart on this timeline")
    chart_id = str(uuid.uuid4())
    chart = {"id": chart_id, "status": "processing", **payload.model_dump(mode="json")}
    timeline.settings_json = {**dict(timeline.settings_json or {}), "data_chart_overlays": [*list(dict(timeline.settings_json or {}).get("data_chart_overlays", [])), chart]}
    db.commit()
    task = generate_chart.delay(str(timeline.id), chart_id)
    return DataChartTaskResponse(chart_id=chart_id, task_id=task.id, timeline_id=timeline.id, status="queued")
