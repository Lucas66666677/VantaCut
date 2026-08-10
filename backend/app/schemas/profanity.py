from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ProfanityFilterRequest(BaseModel):
    user_id: UUID
    sfx_style: Literal["beep", "chicken", "coin"] = "beep"
    emoji_style: Literal["angry", "duck"] = "angry"


class ProfanityFilterResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    status: str
