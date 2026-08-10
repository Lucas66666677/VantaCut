from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class GenerateAudioDescriptionRequest(BaseModel):
    source_asset_id: UUID
    language: str = Field(default="zh", min_length=2, max_length=20)
    min_gap_seconds: float = Field(default=2.0, ge=1.0, le=15.0)
    mode: Literal["standard"] = "standard"


class AudioDescriptionResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str
