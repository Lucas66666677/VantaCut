from typing import Literal

from pydantic import BaseModel, Field


class CandidateSegment(BaseModel):
    id: str
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    transcript: str


class SegmentScore(BaseModel):
    segment_id: str
    semantic_completeness: float = Field(ge=0, le=100)
    presentation_naturalness: float = Field(ge=0, le=100)
    template_alignment: float = Field(ge=0, le=100)
    recommended_action: Literal["keep", "remove"]
    reason: str


class MultimodalScoreResult(BaseModel):
    segment_scores: list[SegmentScore]

