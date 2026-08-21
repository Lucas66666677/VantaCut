from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LecturasRequest(BaseModel):
    avatar_profile_id: UUID
    source_asset_id: UUID
    assistant_name: str = Field(default="Lecturas", min_length=1, max_length=80)
    language: str = Field(default="zh-TW", min_length=2, max_length=20)
    max_interventions: int = Field(default=3, ge=1, le=4)
    use_proxy: bool = True
    confirm_digital_avatar_disclosure: Literal[True]


class LecturasIntervention(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    anchor_output_time: float = Field(ge=0)
    kind: Literal["question", "summary"]
    script: str = Field(min_length=2, max_length=280)
    rationale: str = Field(min_length=2, max_length=400)
    presentation_mode: Literal["freeze", "pip"]
    confidence: float = Field(ge=0, le=1)


class LecturasPlan(BaseModel):
    interventions: list[LecturasIntervention] = Field(default_factory=list, max_length=4)


class LecturasTaskResponse(BaseModel):
    run_id: str
    task_id: str
    source_timeline_id: UUID
    status: str
