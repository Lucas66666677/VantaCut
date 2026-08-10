from typing import Literal

from pydantic import BaseModel, Field


class EducationKeyword(BaseModel):
    term: str = Field(min_length=1, max_length=120)
    category: Literal["advanced_vocabulary", "technical_term", "proper_noun", "grammar_concept"]
    explanation: str = Field(min_length=1, max_length=300)
    importance: float = Field(ge=0, le=100)


class EducationKeywordResult(BaseModel):
    keywords: list[EducationKeyword] = Field(max_length=12)

