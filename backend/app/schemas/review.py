from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ReviewAnnotation(BaseModel):
    canvas_width: int = Field(gt=0)
    canvas_height: int = Field(gt=0)
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=300)


class CreateReviewCommentRequest(BaseModel):
    user_id: UUID
    frame_number: int = Field(ge=0)
    frame_rate: float = Field(gt=0, le=240)
    body: str = Field(min_length=1, max_length=10_000)
    annotation: ReviewAnnotation


class UpdateReviewCommentRequest(BaseModel):
    user_id: UUID
    status: Literal["open", "resolved"]


class ReviewCommentResponse(BaseModel):
    id: UUID
    status: str
    time_seconds: float
    timecode: str
    frame_number: int
    frame_rate: float
    body: str
    annotation: dict[str, Any]
    author_name: str


class ReviewDecisionRequest(BaseModel):
    user_id: UUID
    status: Literal["in_review", "approved", "changes_requested"]
    note: str | None = Field(default=None, max_length=5_000)


class ReviewDecisionResponse(BaseModel):
    timeline_id: UUID
    status: str
    note: str | None


class AddReviewParticipantRequest(BaseModel):
    user_id: UUID
    participant_user_id: UUID
    role: Literal["reviewer", "approver"]
