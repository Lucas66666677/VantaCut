from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LanguageReviewRequest(BaseModel):
    media_asset_id: UUID
    timeline_id: UUID
    target: Literal["ielts_speaking", "advanced_english"] = "ielts_speaking"
    language: Literal["en", "en-us", "en-gb"] = "en"


class LanguageReviewQueuedResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str


class LanguageReviewIssue(BaseModel):
    id: str
    source_start: float
    source_end: float
    output_start: float
    output_end: float
    category: str
    original_text: str
    correction: str
    explanation: str
    confidence: float
    synonyms: list[dict[str, str]] = Field(default_factory=list)
