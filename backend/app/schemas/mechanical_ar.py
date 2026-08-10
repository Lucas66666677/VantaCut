from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


DEFAULT_PART_VOCABULARY = [
    "jumper wire", "breadboard", "DC motor", "servo motor", "ultrasonic sensor",
    "LED", "push button", "gear", "linkage", "LEGO Spike hub", "microcontroller board",
]


class MechanicalARRequest(BaseModel):
    user_id: UUID
    media_asset_id: UUID
    code_asset_id: str | None = Field(default=None, max_length=80)
    use_proxy: bool = True
    sample_fps: float = Field(default=4.0, ge=0.5, le=12.0)
    vocabulary: list[str] = Field(default_factory=lambda: list(DEFAULT_PART_VOCABULARY), min_length=1, max_length=24)


class MechanicalARUploadResponse(BaseModel):
    code_asset_id: str
    timeline_id: UUID
    filename: str
    language: Literal["python", "intel_hex"]


class MechanicalARTaskResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str
