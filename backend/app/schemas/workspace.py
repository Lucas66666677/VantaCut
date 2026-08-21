from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

WorkspaceModuleId = Literal["timeline", "inspector", "color_wheels", "scopes", "audio_mixer"]
WorkspaceMode = Literal["welcome", "editing", "color", "audio"]
WorkspaceRegion = Literal["center", "right", "bottom"]


class WorkspaceModuleLayout(BaseModel):
    enabled: bool = False
    collapsed: bool = False
    region: WorkspaceRegion
    order: int = Field(ge=0, le=20)


class WorkspaceLayoutDocument(BaseModel):
    version: int = Field(default=1, ge=1, le=20)
    mode: WorkspaceMode = "welcome"
    modules: dict[WorkspaceModuleId, WorkspaceModuleLayout]


class WorkspacePreferenceUpdateRequest(BaseModel):
    layout: WorkspaceLayoutDocument


class WorkspacePreferenceResponse(BaseModel):
    project_id: UUID
    layout: WorkspaceLayoutDocument
