from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


TargetLanguage = Literal["en", "ja", "es"]


class LocalizedDubRequest(BaseModel):
    render_job_id: UUID
    voice_profile_id: UUID
    target_language: TargetLanguage
    consent_confirmed: bool = False
    lip_sync_provider: Literal["wav2lip", "sadtalker"] = "wav2lip"
    preserve_background_audio: bool = True

    def model_post_init(self, __context: object) -> None:
        if not self.consent_confirmed:
            raise ValueError("Explicit speaker consent is required for multilingual cloned dubbing")


class LocalizedDubResponse(BaseModel):
    task_id: str
    timeline_id: UUID
    target_language: TargetLanguage
    status: str
