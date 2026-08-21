from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AgentEditRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)


class AgentEditResponse(BaseModel):
    agent_run_id: UUID
    task_id: str
    source_timeline_id: UUID
    status: str


class AgentEditRunResponse(BaseModel):
    id: UUID
    source_timeline_id: UUID
    result_timeline_id: UUID | None
    status: str
    provider_name: str | None
    tool_calls: list[dict[str, Any]]
    message: str | None


class AgentPreviewRequest(BaseModel):
    """Client-side, compact Timeline context used only to plan a ghost proposal."""

    instruction: str = Field(min_length=1, max_length=4000)
    timeline_context: dict[str, Any] = Field(default_factory=dict)


class AgentPreviewResponse(BaseModel):
    provider_name: str
    tool_calls: list[dict[str, Any]]
    explanation: str | None = None


class UndoTimelineRequest(BaseModel):
    pass


class UndoTimelineResponse(BaseModel):
    current_timeline_id: UUID
    restored_from_timeline_id: UUID
    version: int
