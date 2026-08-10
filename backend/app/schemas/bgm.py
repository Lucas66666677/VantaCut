from pydantic import BaseModel, Field, model_validator


class TempoRange(BaseModel):
    min_bpm: int = Field(ge=40, le=220)
    max_bpm: int = Field(ge=40, le=220)

    @model_validator(mode="after")
    def validate_range(self) -> "TempoRange":
        if self.max_bpm < self.min_bpm:
            raise ValueError("max_bpm must not be less than min_bpm")
        return self


class BGMRecommendation(BaseModel):
    mood: str = Field(min_length=1, max_length=300)
    tempo: TempoRange
    search_keywords: list[str] = Field(min_length=3, max_length=5)
