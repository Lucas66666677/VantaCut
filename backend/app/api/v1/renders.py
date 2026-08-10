from datetime import UTC, datetime
from uuid import UUID, uuid4

from celery import chord, group

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import RenderJob, RenderStatus, SubscriptionTier, TemplateLicense, TemplateLicenseStatus, Timeline, User
from app.schemas.render import MatrixExportRequest, MatrixExportResponse, MatrixExportVariantResponse, RenderTimelineRequest, RenderTimelineResponse
from app.services.entitlements import RenderEntitlementError, requires_watermark, validate_render_entitlement
from app.services.media_lifecycle import create_hydration_job, render_assets_needing_hydration
from app.tasks.mam_tasks import restore_hydration_job
from app.tasks.render_tasks import bundle_omnichannel_exports, render_final_timeline
from app.services.distributed_compute import DistributedComputeError, create_batch
from app.services.storage import create_download_url
from app.services.omnichannel_export import MATRIX_PROFILES, build_virtual_timelines, matrix_progress


router = APIRouter(prefix="/timelines", tags=["renders"])


def _authorise_matrix_timeline(db: Session, timeline_id: UUID, user_id: UUID) -> tuple[Timeline, User]:
    timeline, user = db.get(Timeline, timeline_id), db.get(User, user_id)
    if timeline is None or user is None:
        raise HTTPException(status_code=404, detail="Timeline or user not found")
    if timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot export this timeline")
    return timeline, user


def _matrix_response(db: Session, timeline: Timeline, batch_id: UUID) -> MatrixExportResponse:
    batch = dict(dict(timeline.settings_json or {}).get("omnichannel_export_batches", {}).get(str(batch_id), {}))
    if not batch:
        raise HTTPException(status_code=404, detail="Export matrix batch not found")
    live = matrix_progress(str(batch_id)); preview_key = None
    confirmed = dict(timeline.settings_json or {}).get("confirmed_timeline", {})
    source_id = confirmed.get("source_asset_id") if isinstance(confirmed, dict) else None
    if source_id:
        source_asset = db.get(__import__("app.models.entities", fromlist=["MediaAsset"]).MediaAsset, UUID(str(source_id)))
        preview_key = source_asset.thumbnail_key if source_asset else None
    variants: list[MatrixExportVariantResponse] = []
    for item in list(batch.get("variants", [])):
        job = db.get(RenderJob, UUID(str(item["render_job_id"])))
        live_item = dict(live.get(str(item["key"]), {}))
        complete = bool(job and job.status == RenderStatus.COMPLETED and job.output_key)
        variants.append(MatrixExportVariantResponse(
            key=item["key"], aspect_ratio=item["aspect_ratio"], render_job_id=UUID(str(item["render_job_id"])),
            status=(job.status.value if job else "failed"), progress=int(live_item.get("progress", job.progress if job else 0)),
            preview_url=create_download_url(str(job.output_key if complete else preview_key)) if (job.output_key if complete and job else preview_key) else None,
            download_url=create_download_url(str(job.output_key)) if complete and job and job.output_key else None,
            message=str(live_item.get("message") or (job.error_message if job and job.status == RenderStatus.FAILED else "正在等待渲染節點")),
        ))
    return MatrixExportResponse(batch_id=batch_id, status=str(batch.get("status", "queued")), variants=variants, zip_download_url=create_download_url(str(batch["zip_key"]), attachment_filename="omnichannel-export.zip") if batch.get("zip_key") else None, zip_status=batch.get("zip_status"), distribution_targets=[{"platform": "youtube", "variant": "landscape"}, {"platform": "tiktok", "variant": "vertical"}])


@router.get("/render-jobs/{render_job_id}/download-url")
def get_render_download_url(render_job_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> dict[str, str]:
    """Return a short-lived output URL only to the owning editor for Web Share/download."""
    job = db.get(RenderJob, render_job_id)
    user = db.get(User, user_id)
    if job is None or not job.output_key or job.status != RenderStatus.COMPLETED:
        raise HTTPException(status_code=404, detail="Completed render not found")
    if user is None or job.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot access this render")
    return {"download_url": create_download_url(job.output_key, expires_in=3600, attachment_filename=f"{job.id}.{job.output_format}")}


def _render_duration(timeline: Timeline) -> float:
    document = dict(timeline.settings_json or {}).get("confirmed_timeline", {})
    tracks = document.get("tracks", []) if isinstance(document, dict) else []
    segments = (
        [clip for track in tracks if track.get("type") == "main_video" for clip in track.get("clips", [])]
        if tracks else document.get("segments", [])
    )
    return sum(
        float(segment["source_end"]) - float(segment["source_start"])
        for segment in segments
        if segment.get("action", "keep") == "keep"
    )


def _render_asset_ids(timeline: Timeline) -> set[UUID]:
    document = dict(timeline.settings_json or {}).get("confirmed_timeline", {})
    raw_ids: set[UUID] = set()
    source_asset_id = document.get("source_asset_id") if isinstance(document, dict) else None
    if source_asset_id:
        raw_ids.add(UUID(str(source_asset_id)))
    for track in document.get("tracks", []) if isinstance(document, dict) else []:
        for clip in track.get("clips", []):
            source_id = clip.get("source_asset_id")
            if source_id:
                raw_ids.add(UUID(str(source_id)))
    return raw_ids


@router.post("/{timeline_id}/render", response_model=RenderTimelineResponse, status_code=status.HTTP_202_ACCEPTED)
def request_timeline_render(
    timeline_id: UUID,
    payload: RenderTimelineRequest,
    db: Session = Depends(get_db),
) -> RenderTimelineResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    user = db.scalar(select(User).where(User.id == payload.user_id).with_for_update())
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot render this timeline")
    duration = _render_duration(timeline)
    if duration <= 0:
        raise HTTPException(status_code=400, detail="Confirmed timeline has no keep segments")
    # 1080p/4K are source-quality renders. Initiate Glacier restore rather than allowing FFmpeg
    # to fail on a Deep Archive object; proxy playback remains available while this runs.
    if payload.resolution in {"1080p", "4k"}:
        try:
            cold_assets = render_assets_needing_hydration(
                db, project_id=timeline.project_id, asset_ids=_render_asset_ids(timeline)
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if cold_assets:
            hydration = create_hydration_job(db, project=timeline.project, requested_by=user.id, assets=cold_assets)
            db.commit()
            if hydration is not None:
                restore_hydration_job.delay(str(hydration.id))
                raise HTTPException(
                    status_code=202,
                    detail={
                        "code": "cold_storage_hydration_started", "hydration_job_id": str(hydration.id),
                        "message": "正在從冷庫調回高畫質素材，預計需要 12 小時",
                        "estimated_ready_at": hydration.estimated_ready_at.isoformat() if hydration.estimated_ready_at else None,
                    },
                )
    try:
        validate_render_entitlement(user.subscription_tier, duration, payload.resolution)
    except RenderEntitlementError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if user.subscription_tier == SubscriptionTier.FREE and user.render_credits <= 0:
        raise HTTPException(status_code=402, detail="免費渲染點數已用完，請升級 Pro 或購買點數。")

    template_license = None
    if payload.template_license_id:
        template_license = db.scalar(select(TemplateLicense).where(
            TemplateLicense.id == payload.template_license_id
        ).with_for_update())
        if (
            template_license is None or template_license.buyer_id != user.id
            or template_license.timeline_id != timeline.id
            or template_license.status != TemplateLicenseStatus.APPLIED.value
        ):
            raise HTTPException(status_code=403, detail="Marketplace template license is not ready for this timeline")

    job = RenderJob(project_id=timeline.project_id, timeline_id=timeline.id)
    db.add(job)
    if template_license:
        template_license.render_job = job
        template_license.status = TemplateLicenseStatus.RENDERING.value
    if user.subscription_tier == SubscriptionTier.FREE:
        user.render_credits -= 1
    db.commit()
    db.refresh(job)
    if payload.execution_mode == "decentralized":
        try:
            batch = create_batch(
                db, render_job=job, owner_id=user.id, chunk_seconds=5, replication_factor=2,
                resolution=payload.resolution, container_format=payload.container_format,
            )
            db.commit()
        except DistributedComputeError as exc:
            db.rollback()
            if user.subscription_tier == SubscriptionTier.FREE:
                user.render_credits += 1
            job.status = RenderStatus.FAILED
            job.error_message = str(exc)
            db.commit()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return RenderTimelineResponse(
            render_job_id=job.id, task_id=f"distributed:{batch.id}", subscription_tier=user.subscription_tier.value,
            render_credits_remaining=user.render_credits, watermark_applied=requires_watermark(user.subscription_tier),
        )
    try:
        task = render_final_timeline.delay(
            str(job.id), payload.resolution, payload.aspect_ratio, payload.video_codec,
            payload.dynamic_range, payload.bit_depth, payload.audio_loudness_target,
            payload.audio_layout, payload.container_format, payload.include_stem_tracks, payload.spatial_delivery,
        )
    except Exception as exc:
        if user.subscription_tier == SubscriptionTier.FREE:
            user.render_credits += 1
        job.status = RenderStatus.FAILED
        job.error_message = f"Unable to enqueue render: {exc}"
        db.commit()
        raise HTTPException(status_code=503, detail="Render queue is temporarily unavailable") from exc
    return RenderTimelineResponse(
        render_job_id=job.id,
        task_id=task.id,
        subscription_tier=user.subscription_tier.value,
        render_credits_remaining=user.render_credits,
        watermark_applied=requires_watermark(user.subscription_tier),
    )


@router.post("/{timeline_id}/omnichannel-export", response_model=MatrixExportResponse, status_code=status.HTTP_202_ACCEPTED)
def request_omnichannel_export(timeline_id: UUID, payload: MatrixExportRequest, db: Session = Depends(get_db)) -> MatrixExportResponse:
    timeline, user = _authorise_matrix_timeline(db, timeline_id, payload.user_id)
    duration = _render_duration(timeline)
    if duration <= 0:
        raise HTTPException(status_code=400, detail="Confirmed timeline has no keep segments")
    try:
        validate_render_entitlement(user.subscription_tier, duration, payload.resolution)
    except RenderEntitlementError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    credit_cost = len(MATRIX_PROFILES)
    if user.subscription_tier == SubscriptionTier.FREE and user.render_credits < credit_cost:
        raise HTTPException(status_code=402, detail=f"矩陣匯出需要 {credit_cost} 點渲染點數")
    confirmed = dict(timeline.settings_json or {}).get("confirmed_timeline", {})
    try:
        virtual_timelines = build_virtual_timelines(dict(confirmed))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    batch_id = uuid4(); jobs: list[tuple[object, object]] = []
    for profile in MATRIX_PROFILES:
        job = RenderJob(project_id=timeline.project_id, timeline_id=timeline.id)
        db.add(job); db.flush(); jobs.append((profile, job))
    settings_json = dict(timeline.settings_json or {}); batches = dict(settings_json.get("omnichannel_export_batches", {}))
    batches[str(batch_id)] = {"status": "queued", "zip_status": "waiting", "created_at": datetime.now(UTC).isoformat(), "variants": [{"key": profile.key, "aspect_ratio": profile.aspect_ratio, "render_job_id": str(job.id)} for profile, job in jobs]}
    settings_json["omnichannel_export_batches"] = batches; timeline.settings_json = settings_json
    if user.subscription_tier == SubscriptionTier.FREE:
        user.render_credits -= credit_cost
    db.commit()
    try:
        signatures = [render_final_timeline.s(str(job.id), payload.resolution, profile.aspect_ratio, payload.video_codec, "sdr", 10, "streaming", "stereo", payload.container_format, False, "channel_bed", str(batch_id), profile.key, virtual_timelines[profile.key]) for profile, job in jobs]
        chord(group(signatures))(bundle_omnichannel_exports.s(str(timeline.id), str(batch_id)))
    except Exception as exc:
        for _, job in jobs:
            job.status = RenderStatus.FAILED; job.error_message = f"Unable to enqueue matrix render: {exc}"
        db.commit()
        raise HTTPException(status_code=503, detail="Render queue is temporarily unavailable") from exc
    db.refresh(timeline)
    return _matrix_response(db, timeline, batch_id)


@router.get("/{timeline_id}/omnichannel-export/{batch_id}", response_model=MatrixExportResponse)
def get_omnichannel_export(timeline_id: UUID, batch_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> MatrixExportResponse:
    timeline, _ = _authorise_matrix_timeline(db, timeline_id, user_id)
    return _matrix_response(db, timeline, batch_id)
