from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


VoiceEmotion = Literal["neutral", "excited", "calm", "serious", "warm", "sad"]


class CreateVoiceProfileRequest(BaseModel):
    user_id: UUID
    source_media_asset_id: UUID
    name: str = Field(min_length=1, max_length=160)
    language: str | None = Field(default=None, max_length=20)
    consent_confirmed: bool = False

    @model_validator(mode="after")
    def requires_consent(self) -> "CreateVoiceProfileRequest":
        if not self.consent_confirmed:
            raise ValueError("Explicit consent is required before creating a voice profile")
        return self


class VoiceProfileResponse(BaseModel):
    id: UUID
    project_id: UUID
    source_media_asset_id: UUID
    name: str
    status: Literal["queued", "extracting", "ready", "failed"]
    provider_name: str
    quality_score: float | None = None
    task_id: str | None = None


class GenerateVoiceReplacementRequest(BaseModel):
    user_id: UUID
    voice_profile_id: UUID
    cue_id: str = Field(min_length=1, max_length=200)
    replacement_text: str = Field(min_length=1, max_length=600)
    emotion: VoiceEmotion = "neutral"
    tempo: float = Field(default=1.0, ge=.8, le=1.25)
    language: str | None = Field(default=None, max_length=20)
    consent_confirmed: bool = False

    @model_validator(mode="after")
    def requires_consent(self) -> "GenerateVoiceReplacementRequest":
        if not self.consent_confirmed:
            raise ValueError("Explicit consent is required before generating cloned speech")
        return self


class VoiceReplacementResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    voice_profile_id: UUID
    status: str


class VoiceMorphRequest(BaseModel):
    user_id: UUID
    source_media_asset_id: UUID
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    timeline_start: float = Field(ge=0)
    character_id: Literal["robot", "monster", "storybook"]
    consent_confirmed: bool = False

    @model_validator(mode="after")
    def validates_source_and_consent(self) -> "VoiceMorphRequest":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be after source_start")
        if not self.consent_confirmed:
            raise ValueError("Explicit consent is required before transforming speech")
        return self


class VoiceMorphResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str
