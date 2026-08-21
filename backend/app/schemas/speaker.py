from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SpeakerStateRequest(BaseModel):
    pass


class GazeRedirectionRequest(BaseModel):
    confirm_consent: Literal[True] = Field(
        description="The creator explicitly authorises AI-based modification of the speaker's gaze."
    )
    use_proxy: bool = True


class SpeakerTaskResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    status: str
