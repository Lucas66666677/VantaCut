from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


WorkspaceMode = Literal["steam", "landscape", "person", "general"]


class WorkspaceContextResponse(BaseModel):
    timeline_id: UUID
    clip_id: UUID
    mode: WorkspaceMode
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]
    priority_tools: list[str]
