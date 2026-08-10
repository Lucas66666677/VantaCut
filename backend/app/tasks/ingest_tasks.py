"""Low-latency post-processing for independently playable camera chunks."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.ai.providers.factory import get_embedding_provider
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import (
    CameraIngestChunk,
    CameraIngestSession,
    Clip,
    MediaAsset,
    MediaEmbeddingSegment,
    MediaStatus,
    MediaType,
    Timeline,
    TrackType,
)
from app.services.camera_ingest_security import metadata_search_text
from app.services.storage import download_object, upload_object
from app.tasks.media_tasks import _probe
from app.worker import celery_app


class CameraIngestProcessingError(RuntimeError):
    pass


def _run(command: list[str], *, timeout_seconds: int = 10 * 60) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise CameraIngestProcessingError(f"FFmpeg proxy generation timed out after {timeout_seconds}s") from exc
    except subprocess.CalledProcessError as exc:
        raise CameraIngestProcessingError((exc.stderr or "FFmpeg proxy generation failed")[-2000:]) from exc
    except OSError as exc:
        raise CameraIngestProcessingError("ffmpeg/ffprobe is not installed or executable") from exc


def _proxy_chunk(source: Path, destination: Path) -> None:
    _run([
        "ffmpeg", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
        "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast", "-b:v", "1500k",
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(destination),
    ])


def _timeline_clip_payload(
    clip: Clip,
    asset: MediaAsset,
    timeline_start: float,
    sequence_number: int,
    *,
    track: TrackType = TrackType.MAIN_VIDEO,
    camera_label: str | None = None,
    audio_enabled: bool = True,
) -> dict[str, object]:
    return {
        "id": str(clip.id), "source_asset_id": str(asset.id), "source_start": float(clip.source_start),
        "source_end": float(clip.source_end), "timeline_start": round(timeline_start, 3),
        "track": track.value, "z_index": 10 if track == TrackType.MULTICAM_VIDEO else 0,
        "audio_enabled": audio_enabled, "audio_effects": [],
        "action": "keep", "confidence_score": 100,
        "reason": f"攝影機即時片段 #{sequence_number + 1}，已產生代理檔。",
        "sequence_number": sequence_number, "proxy_key": asset.proxy_key,
        "growing": True,
        "camera_label": camera_label,
    }


def _rebuild_growing_timeline(db, session: CameraIngestSession) -> list[dict[str, object]]:
    """Produce ordered timeline positions from ready chunks, including out-of-order arrivals."""
    timeline = db.get(Timeline, session.timeline_id)
    if timeline is None:
        raise CameraIngestProcessingError("Ingest timeline no longer exists")
    chunks = db.scalars(
        select(CameraIngestChunk)
        .where(CameraIngestChunk.session_id == session.id, CameraIngestChunk.status == "ready")
        .order_by(CameraIngestChunk.sequence_number)
    ).all()
    offset = 0.0
    payloads: list[dict[str, object]] = []
    for chunk in chunks:
        if not chunk.media_asset_id or not chunk.duration_seconds:
            continue
        asset = db.get(MediaAsset, chunk.media_asset_id)
        if asset is None:
            continue
        clip = db.scalar(select(Clip).where(Clip.timeline_id == timeline.id, Clip.source_asset_id == asset.id))
        if clip is None:
            # Sparse, deterministic order indices prevent updates from temporarily violating the unique constraint.
            clip = Clip(
                timeline_id=timeline.id, source_asset_id=asset.id, source_start=0,
                source_end=float(chunk.duration_seconds), track=TrackType.MAIN_VIDEO,
                z_index=0, audio_enabled=True, order_index=(chunk.sequence_number + 1) * 1000,
            )
            db.add(clip); db.flush()
        else:
            clip.source_end = float(chunk.duration_seconds)
            clip.enabled = True
        payloads.append(_timeline_clip_payload(clip, asset, offset, chunk.sequence_number))
        offset += float(chunk.duration_seconds)
    settings = dict(timeline.settings_json or {})
    live = dict(settings.get("live_ingest") or {})
    live.update({"session_id": str(session.id), "capture_id": session.capture_id, "status": session.status, "clips": payloads, "duration_seconds": round(offset, 3)})
    timeline.settings_json = {**settings, "live_ingest": live}
    session.total_duration_seconds = offset
    return payloads


def _rebuild_wireless_multicam_timeline(db, session: CameraIngestSession) -> list[dict[str, object]]:
    """Merge two independently arriving phone recordings on their shared server clock.

    ``MediaRecorder`` emits independently playable chunks, so each camera can be
    proxied immediately while its clip retains the original recording offset.
    The first phone remains the audible main angle; additional angles are placed
    on the multicam overlay track with audio disabled to prevent echo.
    """
    timeline = db.get(Timeline, session.timeline_id)
    if timeline is None:
        raise CameraIngestProcessingError("Ingest timeline no longer exists")
    sessions = db.scalars(select(CameraIngestSession).where(CameraIngestSession.timeline_id == timeline.id)).all()
    wireless_sessions = [item for item in sessions if (item.metadata_json or {}).get("wireless_multicam")]
    payloads: list[dict[str, object]] = []
    for camera_session in sorted(wireless_sessions, key=lambda item: int((item.metadata_json or {}).get("wireless_multicam", {}).get("camera_index", 99))):
        info = dict((camera_session.metadata_json or {}).get("wireless_multicam") or {})
        index = int(info.get("camera_index", 1))
        track = TrackType.MAIN_VIDEO if index == 1 else TrackType.MULTICAM_VIDEO
        audio_enabled = index == 1
        timeline_offset = float(info.get("timeline_offset_seconds", 0.0))
        chunks = db.scalars(
            select(CameraIngestChunk).where(CameraIngestChunk.session_id == camera_session.id, CameraIngestChunk.status == "ready").order_by(CameraIngestChunk.sequence_number)
        ).all()
        running_offset = timeline_offset
        for chunk in chunks:
            if not chunk.media_asset_id or not chunk.duration_seconds:
                continue
            asset = db.get(MediaAsset, chunk.media_asset_id)
            if asset is None:
                continue
            clip = db.scalar(select(Clip).where(Clip.timeline_id == timeline.id, Clip.source_asset_id == asset.id))
            if clip is None:
                # order_index is timeline-global, even for different tracks.
                clip = Clip(
                    timeline_id=timeline.id, source_asset_id=asset.id, source_start=0, source_end=float(chunk.duration_seconds),
                    track=track, z_index=10 if track == TrackType.MULTICAM_VIDEO else 0, audio_enabled=audio_enabled,
                    order_index=(index * 1_000_000) + (chunk.sequence_number + 1) * 1000,
                )
                db.add(clip); db.flush()
            else:
                clip.source_end, clip.track, clip.z_index, clip.audio_enabled, clip.enabled = float(chunk.duration_seconds), track, (10 if track == TrackType.MULTICAM_VIDEO else 0), audio_enabled, True
            payloads.append(_timeline_clip_payload(
                clip, asset, running_offset, chunk.sequence_number, track=track, camera_label=str(info.get("label") or f"無線鏡頭 {index}"), audio_enabled=audio_enabled,
            ))
            running_offset += float(chunk.duration_seconds)
    settings = dict(timeline.settings_json or {})
    multicam = dict(settings.get("wireless_multicam") or {})
    multicam.update({"status": "capturing", "clips": payloads})
    timeline.settings_json = {**settings, "wireless_multicam": multicam}
    return payloads


def _index_camera_metadata(db, asset: MediaAsset, metadata: dict[str, object], duration: float) -> None:
    """Metadata is immediately semantically searchable without waiting for frame extraction."""
    try:
        provider = get_embedding_provider()
        if provider.dimensions != 512:
            raise RuntimeError("Embedding provider must use 512 dimensions")
        text = metadata_search_text(metadata)
        vector = provider.embed_text(text)
        db.add(MediaEmbeddingSegment(
            media_asset_id=asset.id, modality="camera_metadata", source_start=0, source_end=max(0.001, duration),
            embedding=vector, metadata_json={"text": text, "provider": provider.name, "camera_metadata": metadata},
        ))
        asset.embedding = vector
        asset.metadata_json = {**(asset.metadata_json or {}), "metadata_embedding": {"provider": provider.name, "text": text}}
    except Exception as exc:  # The ingest/proxy path remains available if a model is temporarily offline.
        asset.metadata_json = {**(asset.metadata_json or {}), "metadata_embedding_error": str(exc)}


@celery_app.task(name="ingest.process_camera_ingest_chunk", bind=True, max_retries=3, default_retry_delay=15)
def process_camera_ingest_chunk(self, chunk_id: str) -> dict[str, object]:
    """Probe, proxy and expose one camera chunk to the editor as soon as it is usable."""
    db = SessionLocal()
    chunk: CameraIngestChunk | None = None
    asset: MediaAsset | None = None
    try:
        chunk = db.get(CameraIngestChunk, UUID(chunk_id))
        if chunk is None:
            raise CameraIngestProcessingError(f"Camera ingest chunk {chunk_id} not found")
        session = db.get(CameraIngestSession, chunk.session_id)
        if session is None:
            raise CameraIngestProcessingError("Camera ingest session not found")
        if chunk.status == "ready" and chunk.media_asset_id:
            rebuild = _rebuild_wireless_multicam_timeline if (session.metadata_json or {}).get("wireless_multicam") else _rebuild_growing_timeline
            clips = rebuild(db, session); db.commit()
            return {"chunk_id": chunk_id, "status": "ready", "clips": clips, "idempotent": True}
        chunk.status = "processing"; db.commit()
        publish_project_status(str(session.project_id), progress=10, stage="camera_chunk_downloading", message="正在接收攝影機片段")

        with tempfile.TemporaryDirectory(prefix=f"camera-ingest-{chunk.id}-") as temporary_directory:
            workdir = Path(temporary_directory)
            source, proxy = workdir / "source.mp4", workdir / "proxy.mp4"
            download_object(chunk.storage_key, str(source))
            metadata = _probe(source)
            duration = float(metadata["duration"])
            if duration <= 0:
                raise CameraIngestProcessingError("Camera chunk must be independently playable with a positive duration")
            publish_project_status(str(session.project_id), progress=35, stage="camera_chunk_proxy", message="正在產生即時預覽代理檔")
            _proxy_chunk(source, proxy)

            asset = db.get(MediaAsset, chunk.media_asset_id) if chunk.media_asset_id else None
            if asset is None:
                asset = MediaAsset(
                    project_id=session.project_id,
                    filename=f"{session.capture_id}-{chunk.sequence_number:06d}.mp4",
                    storage_key=chunk.storage_key, media_type=MediaType.VIDEO, status=MediaStatus.PROCESSING,
                    mime_type=chunk.mime_type, size_bytes=chunk.size_bytes,
                )
                db.add(asset); db.flush(); chunk.media_asset_id = asset.id
            proxy_key = f"projects/{session.project_id}/camera-ingest/{session.id}/proxy/{chunk.sequence_number:09d}.mp4"
            upload_object(proxy_key, str(proxy), "video/mp4")
            asset.duration_seconds = duration
            asset.width, asset.height, asset.fps = int(metadata["width"]), int(metadata["height"]), float(metadata["fps"])
            asset.video_codec = str(metadata.get("video_codec") or "")
            asset.proxy_key = proxy_key
            asset.status = MediaStatus.READY
            asset.metadata_json = {**dict(metadata), "camera": dict(chunk.camera_metadata_json or {}), "ingest_session_id": str(session.id), "sequence_number": chunk.sequence_number}
            chunk.duration_seconds, chunk.proxy_key, chunk.status = duration, proxy_key, "ready"
            _index_camera_metadata(db, asset, dict(chunk.camera_metadata_json or {}), duration)
            rebuild = _rebuild_wireless_multicam_timeline if (session.metadata_json or {}).get("wireless_multicam") else _rebuild_growing_timeline
            clips = rebuild(db, session)
            db.commit()

        publish_project_status(
            str(session.project_id), progress=100, stage="camera_chunk_ready", status="processing",
            message="新的攝影機代理片段已加入時間軸",
            extra={"ingest": {"kind": "growing_timeline", "session_id": str(session.id), "timeline_id": str(session.timeline_id), "clips": clips, "mode": "wireless_multicam" if (session.metadata_json or {}).get("wireless_multicam") else "camera_ingest"}},
        )
        return {"chunk_id": chunk_id, "status": "ready", "asset_id": str(asset.id), "clips": clips}
    except Exception as exc:
        db.rollback()
        if chunk is not None:
            current = db.get(CameraIngestChunk, chunk.id)
            if current is not None:
                current.status, current.error_message = "failed", str(exc)[-2000:]
            if asset is not None:
                current_asset = db.get(MediaAsset, asset.id)
                if current_asset is not None:
                    current_asset.status = MediaStatus.FAILED
                    current_asset.metadata_json = {**(current_asset.metadata_json or {}), "camera_ingest_error": str(exc)}
            db.commit()
            session = db.get(CameraIngestSession, current.session_id)
            if session is not None:
                publish_project_status(str(session.project_id), progress=0, stage="camera_chunk_failed", status="failed", message=str(exc), extra={"ingest": {"kind": "chunk_failed", "chunk_id": str(chunk.id)}})
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(120, 15 * (2 ** self.request.retries)))
        raise
    finally:
        db.close()
