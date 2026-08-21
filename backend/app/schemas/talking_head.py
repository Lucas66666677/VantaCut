from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TalkingHeadConfidenceRequest(BaseModel):
    source_asset_id: UUID
    confidence_threshold: int = Field(default=58, ge=35, le=85)
    enable_gaze_correction: bool = False
    confirm_gaze_correction: Literal[True] | None = None
    use_proxy_for_gaze: bool = True


class TalkingHeadTaskResponse(BaseModel):
    task_id: str
    project_id: UUID
    status: str
    status_sse_path: str
    status_websocket_path: str


class TalkingHeadStatusResponse(BaseModel):
    status: str
    markers: list[dict[str, object]] = []
    gaze_correction: dict[str, object] | None = None
    error: str | None = None
