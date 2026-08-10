from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class DerivedPreviewResponse(BaseModel):
    media_asset_id: UUID
    job_id: str
    kind: Literal["matting", "inpainting"]
    status: str
    preview_url: str | None = None
    error: str | None = None
