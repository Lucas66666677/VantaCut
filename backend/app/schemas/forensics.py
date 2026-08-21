from uuid import UUID

from pydantic import BaseModel


class VerifyForensicRenderRequest(BaseModel):
    pass


class ForensicVerificationResponse(BaseModel):
    render_job_id: UUID
    provenance_key: str | None = None
    stored_metadata: dict[str, object]
    watermark: dict[str, object]
    c2pa: dict[str, object]
