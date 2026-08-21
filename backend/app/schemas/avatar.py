from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AvatarProfileCreate(BaseModel):
    project_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    renderer: Literal["unreal_mrq", "threejs_preview"] = "unreal_mrq"
    asset_bundle_key: str = Field(min_length=1, max_length=1000)
    rig_mapping: dict[str, Any] = Field(default_factory=dict)
    confirm_asset_license: Literal[True]
    confirm_subject_consent: Literal[True]


class AvatarProfileResponse(BaseModel):
    id: UUID
    name: str
    renderer: str
    status: str


class AvatarRenderRequest(BaseModel):
    avatar_profile_id: UUID
    source_asset_id: UUID
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    confirm_subject_consent: Literal[True]

    @model_validator(mode="after")
    def valid_range(self) -> "AvatarRenderRequest":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class AvatarRenderResponse(BaseModel):
    avatar_render_job_id: UUID
    task_id: str
    status: str
