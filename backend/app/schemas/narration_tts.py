from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


NarrationStyle = Literal["energetic_girl", "calm_narrator", "funny_host", "warm_friend", "cool_storyteller"]


class GenerateNarrationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    style: NarrationStyle = "calm_narrator"
    speed: float = Field(default=1.0, ge=.7, le=1.3)
    pitch_semitones: float = Field(default=0, ge=-6, le=6)
    timeline_start: float = Field(default=0, ge=0)
    language: str = Field(default="zh", min_length=2, max_length=20)
    caption_preset: Literal["viral_yellow", "karaoke_pop", "clean_white"] = "viral_yellow"


class GenerateNarrationResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    narration_id: str
    status: str


class NarrationStyleResponse(BaseModel):
    id: NarrationStyle
    label: str
    description: str

