from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class TemplateScene(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    shot_type: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=100)
    pace: Literal["slow", "medium", "fast"]
    dialogue_prompt: str = Field(min_length=1)
    filming_instruction: str = Field(min_length=1)

    @field_validator("end")
    @classmethod
    def end_must_be_after_start(cls, value: float, info: ValidationInfo) -> float:
        start = info.data.get("start")
        if start is not None and value <= start:
            raise ValueError("end must be greater than start")
        return value


class TemplateDocument(BaseModel):
    template_name: str = Field(min_length=1, max_length=200)
    summary: str
    aspect_ratio: str
    overall_pacing: Literal["slow", "medium", "fast", "mixed"]
    scenes: list[TemplateScene] = Field(min_length=1)


class ExtractTemplateRequest(BaseModel):
    media_asset_id: UUID


class TemplateResponse(BaseModel):
    id: UUID
    project_id: UUID
    source_asset_id: UUID | None
    name: str
    structure: TemplateDocument
