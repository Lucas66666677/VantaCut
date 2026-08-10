"""Asynchronous Headless API execution, metering and webhook delivery."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, MediaStatus, MediaType, PlatformAPIKey, PlatformJob, Project
from app.services.ffmpeg_filtergraph import FFmpegFiltergraphBuilder, ExportProfile, run_ffmpeg_render
from app.services.platform_metering import build_invoice, month_bounds, record_usage
from app.services.platform_security import PlatformSecurityError, download_public_video
from app.services.platform_webhooks import WebhookDeliveryError, send_signed_webhook, webhook_payload
from app.services.storage import create_download_url, upload_object
from app.tasks.audio_tasks import analyze_audio_rough_cut
from app.tasks.media_tasks import process_new_media
from app.worker import celery_app


def _rough_cut_timeline(duration: float, markers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bounds = {0.0, duration}
    for marker in markers:
        bounds.add(max(0.0, min(duration, float(marker.get("start", 0)))))
        bounds.add(max(0.0, min(duration, float(marker.get("end", 0)))))
    output: list[dict[str, Any]] = []
    ordered = sorted(bounds)
    for index, (start, end) in enumerate(zip(ordered, ordered[1:])):
        related = [marker for marker in markers if float(marker.get("start", 0)) < end and float(marker.get("end", 0)) > start]
        remove = bool(related)
        output.append({
            "id": f"platform-{index + 1:04d}", "source_start": round(start, 3), "source_end": round(end, 3),
            "action": "remove" if remove else "keep", "confidence_score": 92 if remove else 78,
            "reason": "；".join(str(item.get("reason", item.get("type", "AI marker"))) for item in related) if remove else "No silence or filler marker detected.",
        })
    return [segment for segment in output if segment["source_end"] - segment["source_start"] >= 0.05]


def _normalise_segments(instructions: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    raw = list(instructions.get("segments", []))
    if not raw:
        return [{"source_start": 0.0, "source_end": duration, "action": "keep", "confidence_score": 100, "reason": "Headless full-source render."}]
    segments: list[dict[str, Any]] = []
    for item in raw:
        start, end = float(item["source_start"]), float(item["source_end"])
        if start < 0 or end <= start or end > duration + .01:
            raise ValueError("Each render segment must fit inside the source duration")
        if item.get("action", "keep") == "keep":
            segments.append({"source_start": start, "source_end": end, "action": "keep", "confidence_score": int(item.get("confidence_score", 100)), "reason": str(item.get("reason", "API instruction"))})
    if not segments:
        raise ValueError("Render instructions do not contain any keep segments")
    return segments


def _complete_job(db, job: PlatformJob, *, result: dict[str, Any]) -> None:
    job.status, job.result_json, job.error_message = "completed", result, None
    db.commit()
    deliver_platform_webhook.delay(str(job.id), "video.completed")


def _fail_job(db, job: PlatformJob, exc: Exception) -> None:
    job.status, job.error_message = "failed", str(exc)
    db.commit()
    deliver_platform_webhook.delay(str(job.id), "video.failed")


@celery_app.task(bind=True, name="platform.process_job")
def process_platform_job(self, platform_job_id: str) -> dict[str, Any]:
    db = SessionLocal(); job: PlatformJob | None = None
    try:
        job = db.get(PlatformJob, UUID(platform_job_id))
        if job is None: raise ValueError("Platform job not found")
        if job.status == "completed": return dict(job.result_json)
        api_key = db.get(PlatformAPIKey, job.api_key_id)
        if api_key is None or not api_key.is_active: raise ValueError("Platform API key is inactive")
        job.status = "processing"; db.commit()
        project = Project(owner_id=api_key.owner_id, name=f"API {job.operation} {str(job.id)[:8]}", description="Headless Platform API job")
        db.add(project); db.flush()
        publish_project_status(str(project.id), progress=5, stage="platform_download", message="Downloading third-party source", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"platform-{job.id}-") as temporary:
            workdir = Path(temporary); source_path = workdir / "source.bin"
            content_type, size_bytes = download_public_video(job.source_url, source_path)
            storage_key = f"platform/{api_key.id}/jobs/{job.id}/source"
            upload_object(storage_key, str(source_path), content_type)
            asset = MediaAsset(project_id=project.id, filename="platform-source", storage_key=storage_key, media_type=MediaType.VIDEO, status=MediaStatus.UPLOADING, mime_type=content_type, size_bytes=size_bytes, metadata_json={"platform_job_id": str(job.id), "source_origin": "third_party_url"})
            db.add(asset); db.commit(); db.refresh(asset)
            # apply() executes the existing Celery task synchronously inside this
            # orchestration worker while correctly establishing bound-task context.
            process_new_media.apply(args=[str(asset.id)]).get()
            db.refresh(asset)
            if asset.status != MediaStatus.READY: raise ValueError("Media preprocessing did not complete")
            duration = float(asset.duration_seconds or 0)
            if duration <= 0: raise ValueError("Source has no valid duration")
            record_usage(db, api_key_id=api_key.id, job_id=job.id, metric="source_minutes", quantity=duration / 60, dimensions={"operation": job.operation})
            if job.operation == "rough_cut":
                analyze_audio_rough_cut.apply(args=[str(asset.id)]).get()
                analysis = db.scalar(select(AIAnalysis).where(AIAnalysis.media_asset_id == asset.id, AIAnalysis.analysis_type == AnalysisType.ROUGH_CUT).order_by(AIAnalysis.created_at.desc()))
                if analysis is None or analysis.status != "completed": raise ValueError("Rough-cut analysis did not complete")
                markers = list(analysis.result_json.get("clip_analysis", []))
                timeline = _rough_cut_timeline(duration, markers)
                record_usage(db, api_key_id=api_key.id, job_id=job.id, metric="ai_model_calls", quantity=1, dimensions={"capability": "rough_cut"})
                _complete_job(db, job, result={"project_id": str(project.id), "media_asset_id": str(asset.id), "timeline": {"version": 1, "source_asset_id": str(asset.id), "segments": timeline}, "analysis": {"silences": analysis.result_json.get("silences", []), "filler_markers": analysis.result_json.get("filler_markers", [])}})
                return dict(job.result_json)
            if job.operation == "render":
                segments = _normalise_segments(job.request_json.get("instructions", {}), duration)
                output = workdir / "headless-render.mp4"
                builder = FFmpegFiltergraphBuilder({"segments": segments})
                command = builder.build_command(str(source_path), str(output), export_profile=ExportProfile(resolution=str(job.request_json.get("instructions", {}).get("resolution", "1080p")), aspect_ratio=str(job.request_json.get("instructions", {}).get("aspect_ratio", "16:9"))))
                asyncio.run(run_ffmpeg_render(command, duration_seconds=sum(segment.duration for segment in builder.segments)))
                output_key = f"platform/{api_key.id}/jobs/{job.id}/output.mp4"; upload_object(output_key, str(output), "video/mp4")
                render_minutes = sum(float(item["source_end"]) - float(item["source_start"]) for item in segments) / 60
                record_usage(db, api_key_id=api_key.id, job_id=job.id, metric="render_minutes", quantity=render_minutes, dimensions={"resolution": job.request_json.get("instructions", {}).get("resolution", "1080p")})
                _complete_job(db, job, result={"project_id": str(project.id), "media_asset_id": str(asset.id), "output_key": output_key, "download_url": create_download_url(output_key, expires_in=3600)})
                return dict(job.result_json)
            raise ValueError(f"Unsupported platform operation: {job.operation}")
    except Exception as exc:
        db.rollback()
        if job is not None: _fail_job(db, job, exc)
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="platform.deliver_webhook", autoretry_for=(WebhookDeliveryError,), retry_backoff=True, retry_jitter=True, retry_kwargs={"max_retries": 6})
def deliver_platform_webhook(self, platform_job_id: str, event: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        job = db.get(PlatformJob, UUID(platform_job_id)); api_key = db.get(PlatformAPIKey, job.api_key_id) if job else None
        if job is None or api_key is None or not job.webhook_url or not api_key.encrypted_webhook_secret: return {"skipped": True}
        job.webhook_attempts += 1; db.commit()
        status_code = send_signed_webhook(url=job.webhook_url, encrypted_secret=api_key.encrypted_webhook_secret, event=event, delivery_id=f"{job.id}:{job.webhook_attempts}", payload=webhook_payload(job))
        job.last_webhook_status = status_code; db.commit()
        return {"delivered": True, "status_code": status_code}
    finally:
        db.close()


@celery_app.task(name="platform.generate_monthly_invoices")
def generate_monthly_invoices() -> dict[str, Any]:
    db = SessionLocal()
    try:
        start, end = month_bounds()
        count = 0
        for api_key in db.scalars(select(PlatformAPIKey).where(PlatformAPIKey.is_active.is_(True))).all():
            build_invoice(db, api_key=api_key, period_start=start, period_end=end); count += 1
        db.commit()
        return {"period_start": start.isoformat(), "period_end": end.isoformat(), "invoice_count": count}
    finally:
        db.close()
