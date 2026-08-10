from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


Decision = Literal["keep", "remove"]


class AIFeedbackCreate(BaseModel):
    """Snapshot the signals visible to the user when they corrected an AI proposal."""

    user_id: UUID
    timeline_id: UUID
    clip_id: UUID | None = None
    original_ai_decision: Decision
    user_final_decision: Decision
    clip_context_features: dict[str, Any] = Field(default_factory=dict)


class AIFeedbackResponse(BaseModel):
    id: UUID
    project_id: UUID
    timeline_id: UUID
    clip_id: UUID | None
    original_ai_decision: Decision
    user_final_decision: Decision
    clip_context_features: dict[str, Any]
    created_at: datetime
