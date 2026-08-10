"""Download semantic stock footage, package a short muted B-Roll, then insert it on track two."""
from __future__ import annotations

import copy
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from app.core.config import settings
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus, MediaType, Timeline
from app.services.semantic_stock_broll import PexelsVideoProvider, StockBRollError, extract_scene_keywords
from app.services.storage import upload_object
from app.worker import celery_app


def _tracks_document(confirmed: dict[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(confirmed)
    if "tracks" not in document:
        document["tracks"] = [{"id": "main-video", "type": "main_video", "z_index": 0, "clips": list(document.get("segments", []))}]
    return document


def _download(url: str, destination: Path) -> None:
    received = 0
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=settings.stock_broll_download_timeout_seconds) as response:
            response.raise_for_status()
            with destination.open("wb") as output:
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if received > settings.stock_broll_download_max_bytes:
                        raise StockBRollError("Pexels source video exceeds the configured download limit")
                    output.write(chunk)
    except httpx.HTTPError as exc:
        raise StockBRollError(f"Pexels download failed: {exc}") from exc


def _package(raw: Path, output: Path, *, duration: float, aspect_ratio: str) -> tuple[int, int]:
    width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30,format=yuv420p"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", str(raw), "-t", f"{duration:.3f}", "-map", "0:v:0", "-an", "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-movflags", "+faststart", str(output)], check=True, capture_output=True, text=True, timeout=60 * 20)
    except subprocess.TimeoutExpired as exc:
        raise StockBRollError("Pexels B-Roll packaging timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise StockBRollError((exc.stderr or "Pexels B-Roll packaging failed")[-2000:]) from exc
    return width, height


@celery_app.task(bind=True, name="broll.generate_semantic_stock")
def generate_semantic_stock_broll(self, timeline_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise StockBRollError("Timeline not found")
        source = db.get(MediaAsset, UUID(str(request["source_asset_id"])))
        if source is None or source.project_id != timeline.project_id or source.status != MediaStatus.READY:
            raise StockBRollError("A ready source asset from this project is required")
        settings_json = dict(timeline.settings_json or {})
        candidates = extract_scene_keywords(settings_json, max_clips=int(request["max_clips"]))
        if not candidates:
            raise StockBRollError("No supported concrete scene keywords were found in the timed transcript")
        project_id, job_id = str(timeline.project_id), self.request.id
        settings_json["semantic_stock_broll"] = {"status": "processing", "clips": []}; timeline.settings_json = settings_json; db.commit()
        publish_project_status(project_id, progress=8, stage="semantic_broll_extracting", message="正在從逐字稿辨識可視化場景關鍵字", job_id=job_id)
        confirmed = _tracks_document(dict(settings_json.get("confirmed_timeline", {})))
        broll_track = next((track for track in confirmed["tracks"] if track.get("type") == "b_roll"), None)
        if broll_track is None:
            broll_track = {"id": "semantic-stock-b-roll", "type": "b_roll", "z_index": 10, "clips": []}; confirmed["tracks"].append(broll_track)
        result_clips: list[dict[str, object]] = []
        provider = PexelsVideoProvider(); duration = float(request["duration_seconds"]); aspect_ratio = str(request["aspect_ratio"])
        with tempfile.TemporaryDirectory(prefix=f"semantic-stock-broll-{timeline.id}-") as temporary:
            workdir = Path(temporary)
            for index, candidate in enumerate(candidates):
                progress = 15 + int(index / len(candidates) * 65)
                publish_project_status(project_id, progress=progress, stage="semantic_broll_searching", message=f"正在搜尋「{candidate.label}」的免版稅影片", job_id=job_id)
                stock = provider.search(candidate.query, aspect_ratio=aspect_ratio)
                raw, packaged = workdir / f"pexels-{stock.id}-raw.mp4", workdir / f"pexels-{stock.id}.mp4"
                _download(stock.download_url, raw); width, height = _package(raw, packaged, duration=duration, aspect_ratio=aspect_ratio)
                key = f"projects/{timeline.project_id}/stock-broll/pexels/{job_id}-{index}.mp4"; upload_object(key, str(packaged), "video/mp4")
                asset = MediaAsset(project_id=timeline.project_id, filename=f"pexels-{stock.id}-{candidate.query[:40]}.mp4", storage_key=key, media_type=MediaType.VIDEO, status=MediaStatus.READY, mime_type="video/mp4", size_bytes=packaged.stat().st_size, duration_seconds=duration, width=width, height=height, fps=30, video_codec="h264", metadata_json={"origin": "stock", "provider": "pexels", "pexels_video_id": stock.id, "pexels_url": stock.page_url, "creator": stock.creator, "creator_url": stock.creator_url, "search_query": candidate.query, "attribution_required": True})
                db.add(asset); db.flush()
                clip = {"id": f"semantic-stock-broll-{job_id}-{index}", "source_asset_id": str(asset.id), "source_start": 0, "source_end": duration, "timeline_start": round(candidate.start_time, 3), "track": "b_roll", "z_index": int(broll_track.get("z_index", 10)), "audio_enabled": False, "action": "keep", "kind": "semantic_stock_broll", "fade_in_seconds": .25, "fade_out_seconds": .25, "reason": f"語意關鍵字「{candidate.label}」的 Pexels B-Roll", "confidence_score": round(candidate.confidence * 100), "stock": {"provider": "pexels", "query": candidate.query, "pexels_url": stock.page_url, "creator": stock.creator, "creator_url": stock.creator_url}}
                broll_track["clips"].append(clip); result_clips.append(clip)
        timeline.settings_json = {**dict(timeline.settings_json or {}), "confirmed_timeline": confirmed, "multitrack_timeline": confirmed, "semantic_stock_broll": {"status": "completed", "clips": result_clips}}
        db.commit()
        publish_project_status(project_id, progress=100, stage="semantic_broll_completed", status="completed", message="語意 B-Roll 已插入第二軌並套用淡入淡出", job_id=job_id)
        return {"timeline_id": timeline_id, "clips": result_clips}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "semantic_stock_broll": {"status": "failed", "clips": [], "error": str(exc)}}; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="semantic_broll_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
