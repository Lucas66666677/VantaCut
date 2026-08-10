from __future__ import annotations

import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.config import settings
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import (
    MediaAsset,
    RenderJob,
    RenderStatus,
    SocialAccount,
    SocialPlatform,
    SocialPost,
    SocialPostStatus,
    ThumbnailExperiment,
    ThumbnailExperimentStatus,
    ThumbnailObservation,
    Timeline,
)
from app.services.publishing_metadata import generate_metadata
from app.services.social_platforms import YouTubeClient, access_token_for_account, get_social_client
from app.services.storage import delete_object, download_object, upload_object
from app.worker import celery_app


class SocialPublishingError(RuntimeError):
    pass


def _source_asset(timeline: Timeline, db: Any) -> MediaAsset:
    source_asset_id = dict(timeline.settings_json.get("confirmed_timeline", {})).get("source_asset_id")
    asset = db.get(MediaAsset, UUID(str(source_asset_id))) if source_asset_id else None
    if asset is None:
        raise SocialPublishingError("Timeline has no valid confirmed source asset")
    return asset


def _generate_thumbnail_candidates(post: SocialPost, video_path: Path) -> list[str]:
    """Create three AI candidates through a configurable, provider-neutral worker command."""
    command_template = settings.thumbnail_generation_command
    if not command_template:
        raise SocialPublishingError("THUMBNAIL_GENERATION_COMMAND is required for automatic AI thumbnail generation")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        duration_seconds = max(1.0, float(probe.stdout.strip()))
    except ValueError as exc:
        raise SocialPublishingError("Unable to read rendered video duration for thumbnail generation") from exc
    title = str(post.metadata_json.get("title", ""))
    keys: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"thumbnail-ai-{post.id}-") as temp_dir:
        workdir = Path(temp_dir)
        for index, ratio in enumerate((0.2, 0.5, 0.8), start=1):
            input_frame, output = workdir / f"source-{index}.jpg", workdir / f"candidate-{index}.jpg"
            extracted = subprocess.run(["ffmpeg", "-y", "-ss", f"{duration_seconds * ratio:.3f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(input_frame)], capture_output=True, text=True, timeout=120)
            if extracted.returncode != 0:
                raise SocialPublishingError(f"Thumbnail source-frame extraction failed: {(extracted.stderr or '')[-500:]}")
            prompt = f"High-conversion YouTube thumbnail for: {title}. Use the source frame faithfully; clear subject and no invented claims. Variant {index}."
            generated = subprocess.run(command_template.format(input_frame=str(input_frame), prompt=prompt, output=str(output)), shell=True, capture_output=True, text=True, timeout=300)
            if generated.returncode != 0 or not output.exists():
                raise SocialPublishingError(f"AI thumbnail generator failed: {(generated.stderr or generated.stdout or '')[-500:]}")
            key = f"projects/{post.timeline.project_id}/social/{post.id}/thumbnails/candidate-{index}.jpg"
            upload_object(key, str(output), "image/jpeg")
            keys.append(key)
    return keys


@celery_app.task(bind=True, name="social.generate_metadata_for_timeline")
def generate_metadata_for_timeline(self, timeline_id: str) -> dict[str, Any]:
    db = SessionLocal()
    timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise SocialPublishingError("Timeline not found")
        asset = _source_asset(timeline, db)
        publish_project_status(str(timeline.project_id), progress=20, stage="metadata_preparing", message="正在整理逐字稿與畫面", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"social-metadata-{timeline_id}-") as temp_dir:
            video_path = Path(temp_dir) / "source.mp4"
            download_object(asset.proxy_key or asset.storage_key, str(video_path))
            publish_project_status(str(timeline.project_id), progress=55, stage="metadata_generating", message="AI 正在生成發布文案", job_id=self.request.id)
            metadata = generate_metadata(video_uri=asset.storage_key, video_path=video_path, settings_json=dict(timeline.settings_json))
        timeline.settings_json = {**timeline.settings_json, "publishing_metadata": metadata.model_dump(mode="json")}
        db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="metadata_completed", status="completed", message="發布 Metadata 已生成", job_id=self.request.id)
        return metadata.model_dump(mode="json")
    except Exception as exc:
        db.rollback()
        if timeline:
            publish_project_status(str(timeline.project_id), progress=0, stage="metadata_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="social.publish_timeline")
def publish_timeline(self, social_post_id: str) -> dict[str, Any]:
    db = SessionLocal()
    post: SocialPost | None = None
    try:
        post = db.get(SocialPost, UUID(social_post_id))
        if post is None:
            raise SocialPublishingError("Social post not found")
        account, render = post.social_account, post.render_job
        if render.status != RenderStatus.COMPLETED or not render.output_key:
            raise SocialPublishingError("A completed render is required before publishing")
        post.status = SocialPostStatus.PUBLISHING
        db.commit()
        publish_project_status(str(post.timeline.project_id), progress=20, stage="social_uploading", message=f"正在上傳至 {account.platform.value}", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"social-publish-{post.id}-") as temp_dir:
            video_path = Path(temp_dir) / "final.mp4"
            download_object(render.output_key, str(video_path))
            if post.metadata_json.get("generate_thumbnail_candidates"):
                post.metadata_json = {
                    **post.metadata_json,
                    "thumbnail_candidate_keys": _generate_thumbnail_candidates(post, video_path),
                    "generate_thumbnail_candidates": False,
                }
                db.commit()
            token = access_token_for_account(account)
            outcome = get_social_client(account.platform).publish_video(access_token=token, video_path=video_path, title=str(post.metadata_json.get("title", "Untitled")), description=str(post.metadata_json.get("description", "")), visibility=str(post.metadata_json.get("visibility", "private")), source_key=render.output_key)
        post.platform_post_id = outcome.get("platform_post_id")
        post.status = SocialPostStatus.AWAITING_CREATOR if outcome.get("awaiting_creator") else SocialPostStatus.PUBLISHED
        post.published_at = datetime.now(UTC)
        post.metadata_json = {**post.metadata_json, "platform_response": outcome.get("raw", {})}
        db.commit()
        if account.platform == SocialPlatform.YOUTUBE and post.status == SocialPostStatus.PUBLISHED and post.metadata_json.get("thumbnail_candidate_keys"):
            start_thumbnail_experiment.delay(str(post.id))
        publish_project_status(str(post.timeline.project_id), progress=100, stage="social_published", status="completed", message=f"已提交至 {account.platform.value}", job_id=self.request.id)
        return {"social_post_id": social_post_id, "platform_post_id": post.platform_post_id, "status": post.status.value}
    except Exception as exc:
        db.rollback()
        if post:
            post = db.get(SocialPost, post.id)
            if post:
                post.status, post.error_message = SocialPostStatus.FAILED, str(exc)
                db.commit()
                publish_project_status(str(post.timeline.project_id), progress=0, stage="social_publish_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="social.start_thumbnail_experiment")
def start_thumbnail_experiment(self, social_post_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        post = db.get(SocialPost, UUID(social_post_id))
        if not post or post.social_account.platform != SocialPlatform.YOUTUBE or not post.platform_post_id:
            raise SocialPublishingError("A published YouTube post is required")
        keys = list(post.metadata_json.get("thumbnail_candidate_keys", []))
        if len(keys) != 3:
            raise SocialPublishingError("Exactly three thumbnail candidate object keys are required")
        existing = post.thumbnail_experiment
        if existing:
            return {"experiment_id": str(existing.id), "status": existing.status.value}
        now = datetime.now(UTC)
        experiment = ThumbnailExperiment(social_post_id=post.id, candidates_json=[{"id": f"candidate-{index + 1}", "storage_key": key} for index, key in enumerate(keys)], started_at=now, ends_at=now + timedelta(hours=6))
        db.add(experiment)
        db.flush()
        _activate_candidate(experiment, YouTubeClient(), access_token_for_account(post.social_account))
        db.commit()
        return {"experiment_id": str(experiment.id), "status": experiment.status.value}
    finally:
        db.close()


def _choose_winner(experiment: ThumbnailExperiment) -> str | None:
    scores: dict[str, list[tuple[int, float]]] = {}
    for observation in experiment.observations:
        if observation.impressions is not None and observation.click_through_rate is not None:
            scores.setdefault(observation.candidate_id, []).append((observation.impressions, float(observation.click_through_rate)))
    weighted = {candidate: sum(impressions * ctr for impressions, ctr in values) / max(1, sum(impressions for impressions, _ in values)) for candidate, values in scores.items() if sum(impressions for impressions, _ in values) > 0}
    return max(weighted, key=weighted.get) if weighted else None


def _activate_candidate(experiment: ThumbnailExperiment, client: YouTubeClient, token: str) -> None:
    candidate = experiment.candidates_json[experiment.active_candidate_index]
    with tempfile.TemporaryDirectory(prefix=f"thumbnail-{experiment.id}-") as temp_dir:
        image_path = Path(temp_dir) / "candidate.jpg"
        download_object(candidate["storage_key"], str(image_path))
        client.set_thumbnail(access_token=token, video_id=str(experiment.social_post.platform_post_id), image_path=image_path)


@celery_app.task(name="social.run_thumbnail_experiments")
def run_thumbnail_experiments() -> dict[str, int]:
    """Hourly Celery Beat tick. YouTube allows one active thumbnail, so candidates rotate rather than run concurrently."""
    db = SessionLocal()
    completed = activated = 0
    try:
        experiments = db.scalars(select(ThumbnailExperiment).where(ThumbnailExperiment.status == ThumbnailExperimentStatus.ACTIVE)).all()
        now = datetime.now(UTC)
        for experiment in experiments:
            post, account = experiment.social_post, experiment.social_post.social_account
            try:
                client = YouTubeClient()
                token = access_token_for_account(account)
                active = experiment.candidates_json[experiment.active_candidate_index]
                metrics = client.read_reach_metrics(access_token=token, video_id=str(post.platform_post_id))
                db.add(ThumbnailObservation(experiment_id=experiment.id, candidate_id=active["id"], impressions=metrics.get("impressions"), click_through_rate=metrics.get("ctr"), raw_json=metrics))
                if now >= experiment.ends_at:
                    winner = _choose_winner(experiment)
                    if winner is None:
                        # Reach reports can lag. Preserve the active asset instead of deleting every candidate.
                        experiment.winner_candidate_id = experiment.candidates_json[experiment.active_candidate_index]["id"]
                        experiment.status = ThumbnailExperimentStatus.INSUFFICIENT_DATA
                    else:
                        experiment.winner_candidate_id, experiment.status = winner, ThumbnailExperimentStatus.COMPLETED
                        experiment.active_candidate_index = next(index for index, item in enumerate(experiment.candidates_json) if item["id"] == winner)
                        _activate_candidate(experiment, client, token)
                    experiment.completed_at = now
                    for item in experiment.candidates_json:
                        if item["id"] != experiment.winner_candidate_id:
                            delete_object(item["storage_key"])
                    completed += 1
                else:
                    experiment.active_candidate_index = (experiment.active_candidate_index + 1) % len(experiment.candidates_json)
                    _activate_candidate(experiment, client, token)
                    activated += 1
                db.commit()
            except Exception as exc:
                experiment.status, experiment.error_message = ThumbnailExperimentStatus.FAILED, str(exc)
                db.commit()
        return {"activated": activated, "completed": completed}
    finally:
        db.close()
