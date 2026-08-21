from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AcademicGlossaryEntry(BaseModel):
    term: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(default_factory=list, max_length=12)
    case_sensitive: bool = False


class AcademicModeRequest(BaseModel):
    glossary: list[AcademicGlossaryEntry] = Field(default_factory=list, max_length=300)
    target_programmes: list[str] = Field(default_factory=list, max_length=12)
    language: str = Field(default="en", min_length=2, max_length=20)
    speech_tempo: float = Field(default=.96, ge=.90, le=1.03)
    apply_academic_lut: bool = True


class AcademicTaskResponse(BaseModel):
    task_id: str
    source_timeline_id: UUID
    status: str


class AcademicNarrativeSection(BaseModel):
    kind: Literal["motivation", "methodology", "results", "future_works"]
    target_percent: float = Field(ge=0, le=100)
    evidence_or_visuals: list[str] = Field(default_factory=list)
    narration_guidance: str
    editorial_risk: str


class AcademicNarrativePlan(BaseModel):
    sections: list[AcademicNarrativeSection] = Field(min_length=4, max_length=4)
    overall_note: str
