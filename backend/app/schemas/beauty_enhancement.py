from uuid import UUID

from pydantic import BaseModel, Field


class BeautyEnhancementRequest(BaseModel):
    enabled: bool = True
    skin_smoothing: int = Field(default=35, ge=0, le=100)
    brightness: int = Field(default=8, ge=0, le=100)
    contrast: int = Field(default=10, ge=0, le=100)
    denoise: int = Field(default=30, ge=0, le=100)
    sharpen: int = Field(default=25, ge=0, le=100)


class BeautyEnhancementResponse(BaseModel):
    timeline_id: UUID
    status: str
    beauty_enhancement: dict[str, int | bool]
