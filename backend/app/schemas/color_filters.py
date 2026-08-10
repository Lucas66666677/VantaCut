from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ColorFilterPreset(BaseModel):
    id: str
    name: str
    description: str
    accent: str


class ColorFilterPresetListResponse(BaseModel):
    presets: list[ColorFilterPreset]


class ApplyColorFilterRequest(BaseModel):
    user_id: UUID
    preset_id: str
    intensity: int = Field(default=100, ge=0, le=100, description="Blend percentage from 0 to 100")


class ApplyColorFilterResponse(BaseModel):
    timeline_id: UUID
    status: str
    color_lut: dict[str, object]


class CreateColorMatchRequest(BaseModel):
    user_id: UUID
    reference_image_asset_id: UUID
    source_asset_id: UUID | None = None
    intensity: int = Field(default=100, ge=0, le=100)
    lut_size: Literal[17, 33, 65] = 33


class CreateColorMatchResponse(BaseModel):
    timeline_id: UUID
    status: str
    color_lut: dict[str, object]
    lut_download_url: str
