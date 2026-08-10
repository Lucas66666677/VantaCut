from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TravelMapRequest(BaseModel):
    """A route can be inferred from `route_text` or from the selected timed transcript."""

    user_id: UUID
    route_text: str | None = Field(default=None, max_length=500)
    source_asset_id: UUID | None = None
    timeline_start: float | None = Field(default=None, ge=0)
    duration_seconds: float = Field(default=4.0, ge=2.0, le=8.0)
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    vehicle: Literal["plane", "car"] = "plane"


class TravelMapTaskResponse(BaseModel):
    task_id: str
    project_id: UUID
    status: str
    status_sse_path: str
    status_websocket_path: str


class TravelMapStatusResponse(BaseModel):
    status: str
    clip: dict[str, object] | None = None
    route: list[dict[str, object]] = []
    error: str | None = None
