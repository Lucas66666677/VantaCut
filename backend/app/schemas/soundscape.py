from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


SoundEventKind = Literal["wind", "ambient", "footsteps", "water", "traffic", "room_tone", "other"]
SpatialLayout = Literal["5.1", "7.1.4"]


class SpatialPosition(BaseModel):
    x: float = Field(ge=-1, le=1)
    y: float = Field(ge=-1, le=1)
    z: float = Field(ge=-1, le=1)


class SoundscapeEvent(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    kind: SoundEventKind
    generation_prompt: str = Field(min_length=3, max_length=500)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    gain_db: float = Field(ge=-48, le=0)
    position: SpatialPosition

    @model_validator(mode="after")
    def valid_duration(self) -> "SoundscapeEvent":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must follow start_time")
        return self


class SoundscapePlan(BaseModel):
    events: list[SoundscapeEvent] = Field(default_factory=list, max_length=32)


class SoundscapeGenerationRequest(BaseModel):
    layout: SpatialLayout = "5.1"


class SoundscapeTaskResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str
