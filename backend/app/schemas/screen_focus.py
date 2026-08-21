from uuid import UUID

from pydantic import BaseModel, Field


class ScreenFocusRequest(BaseModel):
    use_proxy: bool = True
    sample_seconds: float = Field(default=0.5, ge=0.2, le=3.0)


class ScreenFocusTaskResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str
