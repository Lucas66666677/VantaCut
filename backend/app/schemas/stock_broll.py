from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SemanticStockBRollRequest(BaseModel):
    source_asset_id: UUID
    aspect_ratio: Literal["16:9", "9:16"] = "9:16"
    duration_seconds: float = Field(default=4.0, ge=3.0, le=5.0)
    max_clips: int = Field(default=3, ge=1, le=5)


class SemanticStockBRollTaskResponse(BaseModel):
    task_id: str
    project_id: UUID
    status: str
    status_sse_path: str
    status_websocket_path: str


class SemanticStockBRollStatusResponse(BaseModel):
    status: str
    clips: list[dict[str, object]] = []
    error: str | None = None
