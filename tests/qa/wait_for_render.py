#!/usr/bin/env python3
"""Wait for the Celery render, then emit a browser-downloadable presigned MP4 URL."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from uuid import UUID

from app.db.session import SessionLocal
from app.models.entities import RenderJob, RenderStatus
from app.services.storage import create_download_url


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--render-job-id", required=True); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(); deadline = time.monotonic() + args.timeout; db = SessionLocal()
    try:
        while time.monotonic() < deadline:
            db.expire_all(); job = db.get(RenderJob, UUID(args.render_job_id))
            if job is None: raise SystemExit("Render job disappeared")
            if job.status == RenderStatus.COMPLETED:
                if not job.output_key: raise SystemExit("Completed render has no object-storage key")
                args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps({"render_job_id": str(job.id), "output_key": job.output_key, "download_url": create_download_url(job.output_key, expires_in=3600, attachment_filename="qa-render.mp4")}, indent=2), encoding="utf-8")
                return
            if job.status == RenderStatus.FAILED: raise SystemExit(f"Render failed: {job.error_message}")
            time.sleep(2)
    finally:
        db.close()
    raise SystemExit("Timed out waiting for Celery render")


if __name__ == "__main__": main()
