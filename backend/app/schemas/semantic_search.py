from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MediaSemanticSearchRequest(BaseModel):
    project_id: UUID
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=12, ge=1, le=50)


class MediaSemanticSearchResult(BaseModel):
    media_asset_id: UUID
    filename: str
    thumbnail_key: str | None
    thumbnail_url: str | None = None
    source_duration: float | None = None
    source_start: float
    source_end: float
    modality: Literal["keyframe", "transcript", "visual_caption", "camera_metadata"]
    similarity_score: float = Field(ge=0, le=1)
    matched_text: str | None = None


class MediaSemanticSearchResponse(BaseModel):
    query: str
    results: list[MediaSemanticSearchResult]


class MediaSemanticGridRequest(BaseModel):
    project_id: UUID
    limit: int = Field(default=120, ge=1, le=300)


class MediaSemanticGridItem(BaseModel):
    media_asset_id: UUID
    filename: str
    thumbnail_url: str | None = None
    source_start: float = 0
    source_end: float
    cluster_x: float = Field(ge=0, le=1)
    cluster_y: float = Field(ge=0, le=1)
    cluster_label: str


class MediaSemanticGridResponse(BaseModel):
    items: list[MediaSemanticGridItem]
