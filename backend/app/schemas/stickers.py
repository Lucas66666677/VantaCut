from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RecommendStickersRequest(BaseModel):
    user_id: UUID
    enabled: bool = True


class ToggleAIStickersRequest(BaseModel):
    user_id: UUID
    enabled: bool


class StickerTransform(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    scale: float = Field(ge=0.2, le=4)
    rotation: float = Field(ge=-180, le=180)


class StickerTransformRequest(BaseModel):
    user_id: UUID
    transform: StickerTransform
    source: Literal["ai", "user"] = "user"


class StickerResponse(BaseModel):
    timeline_id: UUID
    status: str
    enabled: bool
    items: list[dict[str, object]]

