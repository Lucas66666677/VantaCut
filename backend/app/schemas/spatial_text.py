from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field

class SpatialTrackingRequest(BaseModel):
    user_id: UUID
    use_proxy: bool = True

class SpatialTrackingResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    status: str

class SpatialTextRequest(BaseModel):
    user_id: UUID
    source_asset_id: UUID
    text: str = Field(min_length=1, max_length=240)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    z: float = Field(ge=0, le=1)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    color: str = Field(default="#ffffff", max_length=16)
