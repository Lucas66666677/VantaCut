from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class StemExtractionRequest(BaseModel):
    model_name: Literal["htdemucs", "htdemucs_ft", "htdemucs_6s"] = "htdemucs"


class StemExtractionResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    status: str


class ParametricEQBand(BaseModel):
    frequency_hz: float = Field(ge=20, le=20_000)
    width_octaves: float = Field(default=1.0, gt=0.05, le=6.0)
    gain_db: float = Field(ge=-24, le=24)


class StemChannelSettings(BaseModel):
    gain_db: float = Field(default=0, ge=-48, le=24)
    mute: bool = False
    eq: list[ParametricEQBand] = Field(default_factory=list, max_length=8)


class StemMixSettingsRequest(BaseModel):
    source_asset_id: UUID
    dialogue: StemChannelSettings = Field(default_factory=StemChannelSettings)
    music: StemChannelSettings = Field(default_factory=StemChannelSettings)
    sfx: StemChannelSettings = Field(default_factory=StemChannelSettings)


class StemMixSettingsResponse(BaseModel):
    timeline_id: UUID
    source_asset_id: UUID
    status: str
