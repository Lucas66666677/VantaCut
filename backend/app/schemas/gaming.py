from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class GamingHighlightRequest(BaseModel):
    media_asset_id: UUID
    microphone_track_index: int = Field(default=0, ge=0, le=7)
    system_track_index: int = Field(default=1, ge=0, le=7)
    kill_feed_region: tuple[float, float, float, float] = (0.62, 0.0, 1.0, 0.35)

    @model_validator(mode="after")
    def validate_kill_feed_region(self) -> "GamingHighlightRequest":
        left, top, right, bottom = self.kill_feed_region
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError("kill_feed_region must be normalized as (left, top, right, bottom)")
        return self


class GamingHighlightQueuedResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    status: str
