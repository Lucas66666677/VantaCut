"""Batch long-form to three independent, renderable short-video timelines."""
from __future__ import annotations

import json
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.ai.providers.factory import get_text_provider
from app.ai.providers.schemas import Transcript, TranscriptSegment, WordTimestamp
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, Clip, MediaAsset, RenderJob, RenderStatus, Timeline, TrackType, User
from app.schemas.subtitle import SubtitleCue
from app.services.beat_sync import analyze_visual_momentum
from app.services.long_to_shorts import fallback_hook_title, select_short_candidates
from app.services.storage import create_download_url, download_object, upload_bytes, upload_object
from app.services.subtitles import cues_to_ass, cues_to_srt, transcript_to_cues
from app.services.kinetic_subtitles import annotate_transcript_kinetics
from app.tasks.render_tasks import render_final_timeline
from app.worker import celery_app


def _title(text: str, index: int) -> str:
    try:
        result = get_text_provider().generate_structured_json(
            system_prompt="You write one concise, conversational Chinese short-video opening hook. Return JSON only.",
            user_prompt=f"Turn this excerpt into one hook title under 22 Chinese characters, no hashtags:\n{text[:1600]}",
            response_schema={"type": "object", "required": ["title"], "properties": {"title": {"type": "string", "maxLength": 44}}},
        )
        title = str(result.get("title", "")).strip()
        if 2 <= len(title) <= 44:
            return title
    except Exception:
        pass
    return fallback_hook_title(text, index)


def _relative_cues(raw_transcript: dict[str, Any], start: float, end: float) -> list[SubtitleCue]:
    segments: list[TranscriptSegment] = []
    for raw in raw_transcript.get("segments", []):
        segment_start, segment_end = float(raw.get("start", 0)), float(raw.get("end", 0))
        if segment_end <= start or segment_start >= end:
            continue
        words = [WordTimestamp.model_validate({**word, "start": max(0.0, float(word.get("start", segment_start)) - start), "end": max(0.0, min(end, float(word.get("end", segment_end))) - start)}) for word in raw.get("words", []) if float(word.get("end", segment_end)) > start and float(word.get("start", segment_start)) < end]
        segments.append(TranscriptSegment(text=str(raw.get("text", "")), start=max(0.0, segment_start - start), end=max(.01, min(end, segment_end) - start), words=words))
    transcript = Transcript(text=" ".join(item.text for item in segments), segments=segments, provider="source-analysis", model="reused-asr")
    annotate_transcript_kinetics(transcript)
    return transcript_to_cues(transcript, 0, 1)


@celery_app.task(bind=True, name="long_to_shorts.generate")
def generate_long_to_shorts(self, source_timeline_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); parent: Timeline | None = None
    try:
        parent, asset = db.get(Timeline, UUID(source_timeline_id)), db.get(MediaAsset, UUID(str(request["source_media_asset_id"])))
        user = db.get(User, UUID(str(request["user_id"])))
        if parent is None or asset is None or user is None or parent.project_id != asset.project_id or parent.project.owner_id != user.id or not asset.proxy_key:
            raise ValueError("Long-to-Shorts source video is unavailable")
        analysis = db.scalar(select(AIAnalysis).where(AIAnalysis.media_asset_id == asset.id, AIAnalysis.analysis_type == AnalysisType.ROUGH_CUT, AIAnalysis.status == "completed").order_by(AIAnalysis.created_at.desc()))
        if analysis is None or not dict(analysis.result_json or {}).get("transcript"):
            raise ValueError("Run audio analysis/ASR before generating Shorts")
        result = dict(analysis.result_json or {}); transcript = dict(result["transcript"])
        publish_project_status(str(parent.project_id), progress=12, stage="long_to_shorts_understanding", message="正在分析逐字稿資訊密度與畫面動量", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"long-to-shorts-{parent.id}-") as temporary:
            proxy = Path(temporary) / "proxy.mp4"; download_object(asset.proxy_key or asset.storage_key, str(proxy))
            visual_events = [asdict(item) for item in analyze_visual_momentum(proxy, sample_fps=4)]
        candidates = select_short_candidates(transcript=transcript, duration_seconds=float(asset.duration_seconds or 0), visual_events=visual_events, count=3, min_duration=float(request.get("min_duration_seconds", 45)), max_duration=float(request.get("max_duration_seconds", 60)))
        if len(candidates) != 3:
            raise ValueError("Unable to identify three usable 45–60 second clips")
        shorts: list[dict[str, Any]] = []
        base_version = int(db.query(Timeline).filter(Timeline.project_id == parent.project_id).count())
        for index, candidate in enumerate(candidates):
            title = _title(str(candidate.get("text", "")), index)
            short = Timeline(project_id=parent.project_id, parent_timeline_id=parent.id, name=f"Short {index + 1} · {title}", version=base_version + index + 1, is_current=False)
            db.add(short); db.flush()
            clip_id = uuid4(); start, end = float(candidate["source_start"]), float(candidate["source_end"])
            document = {"schema": "com.aivideo.long-to-shorts.v1", "source_asset_id": str(asset.id), "segments": [{"id": str(clip_id), "source_asset_id": str(asset.id), "source_start": start, "source_end": end, "action": "keep", "confidence_score": min(100, round(60 + float(candidate["score"]) * 25)), "reason": "Long-to-Shorts information-density and visual-motion selection"}], "tracks": [{"id": "main-video", "type": "main_video", "z_index": 0, "clips": [{"id": str(clip_id), "source_asset_id": str(asset.id), "source_start": start, "source_end": end, "action": "keep", "confidence_score": min(100, round(60 + float(candidate["score"]) * 25)), "reason": "Long-to-Shorts information-density and visual-motion selection"}]}]}
            cues = _relative_cues(transcript, start, end)
            subtitle_base = f"projects/{parent.project_id}/timelines/{short.id}/subtitles"
            srt_key, ass_key = f"{subtitle_base}/subtitles.srt", f"{subtitle_base}/subtitles.ass"
            upload_bytes(srt_key, cues_to_srt(cues).encode("utf-8"), "application/x-subrip"); upload_bytes(ass_key, cues_to_ass(cues, preset="viral_yellow", aspect_ratio="9:16").encode("utf-8"), "text/x-ssa")
            short.settings_json = {"confirmed_timeline": document, "multitrack_timeline": document, "auto_reframe": {"enabled": True, "detector_stride": 2, "smoothing": .78, "max_pan_speed_px_per_second": 720}, "caption_style": {"preset": "viral_yellow", "aspect_ratio": "9:16"}, "subtitles": {"status": "completed", "items": [cue.model_dump(mode="json") for cue in cues], "srt_key": srt_key, "ass_key": ass_key, "render_mode": "ass", "caption_preset": "viral_yellow", "caption_aspect_ratio": "9:16"}, "short_hook": {"title": title, "style": "conversational_opening", "source": "text_and_visual_ranked"}, "long_to_shorts": {"candidate": candidate, "source_timeline_id": str(parent.id)}}
            db.add(Clip(id=clip_id, timeline_id=short.id, source_asset_id=asset.id, source_start=start, source_end=end, track=TrackType.MAIN_VIDEO, z_index=0, audio_enabled=True, order_index=0)); shorts.append({"timeline_id": str(short.id), "title": title, "source_start": start, "source_end": end, "duration": end - start, "score": candidate["score"]})
            publish_project_status(str(parent.project_id), progress=30 + int((index + 1) / 3 * 55), stage="long_to_shorts_assembling", message=f"正在建立短片 {index + 1}/3", job_id=self.request.id)
        settings = dict(parent.settings_json or {}); settings["long_to_shorts"] = {"status": "completed", "source_asset_id": str(asset.id), "shorts": shorts, "visual_events": visual_events[:120]}; parent.settings_json = settings; db.commit()
        publish_project_status(str(parent.project_id), progress=100, stage="long_to_shorts_ready", status="completed", message="已建立 3 支可預覽的短片版本", job_id=self.request.id)
        return {"timeline_id": str(parent.id), "shorts": shorts}
    except Exception as exc:
        db.rollback()
        if parent is not None:
            settings = dict(parent.settings_json or {}); settings["long_to_shorts"] = {"status": "failed", "error": str(exc)}; parent.settings_json = settings; db.commit()
            publish_project_status(str(parent.project_id), progress=0, stage="long_to_shorts_failed", status="failed", message="長片轉短片失敗", job_id=self.request.id)
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="long_to_shorts.export_batch")
def export_long_to_shorts_batch(self, source_timeline_id: str, user_id: str, resolution: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        parent, user = db.get(Timeline, UUID(source_timeline_id)), db.get(User, UUID(user_id))
        if parent is None or user is None or parent.project.owner_id != user.id:
            raise ValueError("Batch export access changed")
        record = dict((parent.settings_json or {}).get("long_to_shorts", {})); shorts = list(record.get("shorts", []))
        if record.get("status") != "completed" or len(shorts) != 3:
            raise ValueError("Generate three Shorts before batch export")
        jobs: list[RenderJob] = []
        for item in shorts:
            job = RenderJob(project_id=parent.project_id, timeline_id=UUID(str(item["timeline_id"]))); db.add(job); db.flush(); jobs.append(job)
        record.update({"status": "rendering", "render_job_ids": [str(job.id) for job in jobs], "resolution": resolution}); settings = dict(parent.settings_json or {}); settings["long_to_shorts"] = record; parent.settings_json = settings; db.commit()
        for job in jobs:
            render_final_timeline.delay(str(job.id), resolution, "9:16")
        package_long_to_shorts_zip.apply_async(args=[source_timeline_id, [str(job.id) for job in jobs]], countdown=12)
        return {"timeline_id": source_timeline_id, "render_job_ids": [str(job.id) for job in jobs]}
    finally:
        db.close()


@celery_app.task(bind=True, name="long_to_shorts.package_zip", max_retries=300)
def package_long_to_shorts_zip(self, source_timeline_id: str, render_job_ids: list[str]) -> dict[str, Any]:
    db = SessionLocal(); parent: Timeline | None = None
    try:
        parent = db.get(Timeline, UUID(source_timeline_id)); jobs = [db.get(RenderJob, UUID(item)) for item in render_job_ids]
        if parent is None or any(job is None for job in jobs): raise ValueError("Short render jobs are unavailable")
        if any(job.status in {RenderStatus.QUEUED, RenderStatus.PROCESSING} for job in jobs): raise self.retry(countdown=12)
        completed = [job for job in jobs if job.status == RenderStatus.COMPLETED and job.output_key]
        if len(completed) != len(jobs): raise ValueError("One or more Shorts failed to render")
        with tempfile.TemporaryDirectory(prefix=f"shorts-zip-{parent.id}-") as temporary:
            workdir, archive_path = Path(temporary), Path(temporary) / "shorts.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for index, job in enumerate(completed, start=1):
                    local = workdir / f"short-{index}.{job.output_format}"; download_object(str(job.output_key), str(local)); archive.write(local, arcname=f"short-{index}.{job.output_format}")
            key = f"projects/{parent.project_id}/long-to-shorts/{uuid4()}/shorts.zip"; upload_object(key, str(archive_path), "application/zip")
        settings = dict(parent.settings_json or {}); record = dict(settings.get("long_to_shorts", {})); record.update({"status": "exported", "zip_key": key}); settings["long_to_shorts"] = record; parent.settings_json = settings; db.commit()
        publish_project_status(str(parent.project_id), progress=100, stage="long_to_shorts_exported", status="completed", message="3 支短片已打包完成", job_id=self.request.id)
        return {"zip_key": key}
    finally:
        db.close()
