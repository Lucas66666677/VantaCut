"""Authenticated verification endpoint for an exported asset's two forensic layers."""
from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import RenderJob, User
from app.schemas.forensics import ForensicVerificationResponse, VerifyForensicRenderRequest
from app.services.forensic_provenance import ForensicError, extract_forensic_watermark, verify_c2pa_asset
from app.services.storage import download_object

router = APIRouter(prefix="/renders", tags=["forensics"])


@router.post("/{render_job_id}/forensics/verify", response_model=ForensicVerificationResponse)
def verify_exported_render(
    render_job_id: UUID,
    payload: VerifyForensicRenderRequest,
    db: Session = Depends(get_db),
) -> ForensicVerificationResponse:
    render_job, user = db.get(RenderJob, render_job_id), db.get(User, payload.user_id)
    if render_job is None or not render_job.output_key:
        raise HTTPException(status_code=404, detail="Completed render not found")
    if user is None or render_job.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot verify this render")
    with tempfile.TemporaryDirectory(prefix="verify-forensic-") as temp_dir:
        asset = Path(temp_dir) / f"asset.{render_job.output_format}"
        download_object(render_job.output_key, str(asset))
        try:
            watermark = extract_forensic_watermark(asset)
        except ForensicError as exc:
            watermark = {"detected": False, "error": str(exc)}
        try:
            c2pa = verify_c2pa_asset(asset)
        except (ForensicError, OSError) as exc:
            c2pa = {"available": False, "error": str(exc)}
    return ForensicVerificationResponse(
        render_job_id=render_job.id,
        provenance_key=render_job.provenance_key,
        stored_metadata=dict(render_job.forensic_metadata_json or {}),
        watermark=watermark,
        c2pa=c2pa,
    )
