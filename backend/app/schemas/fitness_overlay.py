from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class FitnessOverlayRequest(BaseModel):
    user_id: UUID
    source_asset_id: UUID
    exercise: Literal["squat", "bench_press", "deadlift"] = "squat"
    hud_style: Literal["impact", "neon", "minimal"] = "impact"
    target_reps: int = Field(default=10, ge=1, le=100)
    sample_every_n_frames: int = Field(default=3, ge=1, le=12)
    fatigue_ratio: float = Field(default=1.25, ge=1.05, le=2.5)


class FitnessOverlayTaskResponse(BaseModel):
    task_id: str
    project_id: UUID
    status: str
    status_sse_path: str
    status_websocket_path: str


class FitnessOverlayStatusResponse(BaseModel):
    status: str
    rep_count: int = 0
    events: list[dict[str, object]] = []
    fatigue_event: dict[str, object] | None = None
    error: str | None = None
