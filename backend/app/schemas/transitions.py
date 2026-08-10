from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


TransitionKind = Literal["crossfade", "glitch", "rgb_split", "zoom_blur", "depth_person_through", "depth_background_peel", "morph_cut"]


class TransitionSpec(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    from_clip_id: UUID
    to_clip_id: UUID
    kind: TransitionKind
    duration_seconds: float = Field(default=.35, gt=.05, le=2.0)
    source_asset_id: UUID | None = None
    target_asset_id: UUID | None = None
    from_source_time: float | None = Field(default=None, ge=0)
    to_source_time: float | None = Field(default=None, ge=0)
    shader_id: str | None = Field(default=None, max_length=120)
    render_asset_key: str | None = None
    fallback_xfade: str | None = None

    @model_validator(mode="after")
    def asset_transition_requires_boundary_frames(self) -> "TransitionSpec":
        if self.kind in {"depth_person_through", "depth_background_peel", "morph_cut"}:
            if None in {self.source_asset_id, self.target_asset_id, self.from_source_time, self.to_source_time}:
                raise ValueError("Depth and morph transitions require source/target assets and boundary times")
        return self


class TimelineTransitionsRequest(BaseModel):
    user_id: UUID
    transitions: list[TransitionSpec] = Field(default_factory=list, max_length=100)


class TransitionBuildResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    transition_id: str
    status: str
