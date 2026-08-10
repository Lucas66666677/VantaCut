from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


PlatformName = Literal["youtube", "tiktok"]


class OAuthAuthorizationResponse(BaseModel):
    authorization_url: str
    platform: PlatformName


class SocialAccountResponse(BaseModel):
    id: UUID
    platform: PlatformName
    platform_account_id: str
    display_name: str | None
    scopes: list[str]
    token_expires_at: datetime | None


class Chapter(BaseModel):
    start_time: float = Field(ge=0)
    title: str = Field(min_length=1, max_length=100)


class GeneratedSocialMetadata(BaseModel):
    titles: list[str] = Field(min_length=3, max_length=3)
    description: str = Field(max_length=5000)
    seo_keywords: list[str] = Field(min_length=5, max_length=12)
    hashtags: list[str] = Field(min_length=3, max_length=8)
    chapters: list[Chapter] = Field(min_length=1)


class MetadataGenerationResponse(BaseModel):
    timeline_id: UUID
    task_id: str


class PublishTimelineRequest(BaseModel):
    user_id: UUID
    social_account_id: UUID
    render_job_id: UUID
    title: str = Field(min_length=1, max_length=2200)
    description: str = Field(default="", max_length=5000)
    visibility: Literal["public", "unlisted", "private", "self_only"] = "private"
    # Supply three generated keys, or leave empty to use THUMBNAIL_GENERATION_COMMAND.
    thumbnail_candidate_keys: list[str] = Field(default_factory=list, max_length=3)
    start_thumbnail_experiment: bool = False


class PublishTimelineResponse(BaseModel):
    social_post_id: UUID
    task_id: str
    status: str


class ThumbnailExperimentResponse(BaseModel):
    experiment_id: UUID
    task_id: str
    candidate_count: int
