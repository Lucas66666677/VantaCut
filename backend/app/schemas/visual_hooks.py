from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class VisualHooksRequest(BaseModel):
    user_id: UUID
    enabled: bool = True
    style: Literal["gradient_line", "liquid_fill", "border_marquee"] = "gradient_line"
    platform: Literal["tiktok", "instagram_reels", "youtube_shorts"] = "tiktok"
    suspense_enabled: bool = True


class VisualHooksResponse(BaseModel):
    timeline_id: UUID
    status: str
    style: str
    platform: str
    highlight_time: float | None = None
    suspense_text: str | None = None
