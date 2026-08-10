from typing import Any, Literal

from pydantic import BaseModel, Field


class WordTimestamp(BaseModel):
    word: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    emotion: Literal["neutral", "emphasis", "surprise", "anger", "joy", "sadness"] = "neutral"
    emotion_intensity: float = Field(default=0.0, ge=0, le=1)
    animation_preset: Literal["none", "spring", "pop", "shake", "explode", "float"] = "none"
    # Kept on each token so a browser preview and an exported caption use the same emphasis.
    highlight_kind: Literal["none", "verb", "number"] = "none"


class TranscriptSegment(BaseModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    words: list[WordTimestamp] = Field(default_factory=list)


class DeliveryHint(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    kind: str
    message: str
    suggested_action: str
    confidence: float = Field(ge=0, le=1)
    words_per_minute: float | None = Field(default=None, ge=0)


class Transcript(BaseModel):
    language: str | None = None
    text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    provider: str
    model: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    delivery_hints: list[DeliveryHint] = Field(default_factory=list)
