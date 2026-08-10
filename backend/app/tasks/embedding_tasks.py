"""Build pgvector embeddings for proxy-video keyframes and ASR transcript segments."""
from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select

from app.ai.providers.factory import get_embedding_provider, get_vision_provider
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, MediaEmbeddingSegment, MediaStatus
from app.services.storage import download_object
from app.worker import celery_app


KEYFRAME_COUNT = 8
VISUAL_SEMANTIC_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "scene": {"type": "string"}, "objects": {"type": "array", "items": {"type": "string"}},
        "people_emotion": {"type": "string"}, "action": {"type": "string"},
    }, "required": ["scene", "objects", "people_emotion", "action"],
}


def _visual_caption(frame_path: Path, timestamp: float) -> dict[str, Any]:
    """Provider-neutral, structured keyframe description; graceful fallback retains CLIP-only search."""
    try:
        response = get_vision_provider().analyze_video(
            str(frame_path),
            "Describe this single video keyframe for semantic media retrieval. Identify scene, concrete objects, visible human emotion (or unknown), and action. Return strict JSON.",
            response_schema=VISUAL_SEMANTIC_SCHEMA,
            context={"task": "semantic_keyframe_caption", "timestamp": timestamp, "frame_path": str(frame_path)},
        )
        scene = str(response.get("scene", "unknown scene")).strip()
        objects = [str(item).strip() for item in response.get("objects", []) if str(item).strip()][:12]
        emotion = str(response.get("people_emotion", "unknown")).strip()
        action = str(response.get("action", "unknown action")).strip()
        return {"scene": scene, "objects": objects, "people_emotion": emotion, "action": action}
    except Exception:
        return {"scene": "unlabelled visual scene", "objects": [], "people_emotion": "unknown", "action": "unknown"}


def _normalised_mean(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("Cannot aggregate an empty embedding set")
    dimension = len(vectors[0])
    if any(len(vector) != dimension for vector in vectors):
        raise ValueError("Embedding dimension mismatch")
    averaged = [sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimension)]
    magnitude = math.sqrt(sum(value * value for value in averaged)) or 1.0
    return [value / magnitude for value in averaged]


def _extract_keyframes(proxy_path: Path, output_dir: Path, duration_seconds: float) -> list[tuple[float, Path]]:
    timestamps = [duration_seconds * index / (KEYFRAME_COUNT + 1) for index in range(1, KEYFRAME_COUNT + 1)]
    frames: list[tuple[float, Path]] = []
    for index, timestamp in enumerate(timestamps):
        path = output_dir / f"keyframe-{index:02d}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(proxy_path), "-frames:v", "1", "-q:v", "3", str(path)],
                check=True, capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Unable to extract semantic-search keyframe at {timestamp:.3f}s") from exc
        frames.append((timestamp, path))
    return frames


def _latest_transcript_segments(db, asset_id: UUID) -> list[dict[str, Any]]:
    analysis = db.scalars(
        select(AIAnalysis)
        .where(AIAnalysis.media_asset_id == asset_id, AIAnalysis.analysis_type == AnalysisType.ROUGH_CUT, AIAnalysis.status == "completed")
        .order_by(AIAnalysis.created_at.desc())
    ).first()
    if analysis is None:
        return []
    transcript = dict(analysis.result_json or {}).get("transcript", {})
    return [segment for segment in transcript.get("segments", []) if segment.get("text")]


@celery_app.task(name="media.generate_media_embeddings")
def generate_media_embeddings(asset_id: str) -> dict[str, Any]:
    """Idempotently rebuild searchable keyframe/transcript embeddings for one asset."""
    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(asset_id))
        if asset is None:
            raise LookupError(f"Media asset {asset_id} not found")
        if asset.status != MediaStatus.READY or not asset.proxy_key:
            raise RuntimeError("Media must be ready with a proxy before embedding")
        provider = get_embedding_provider()
        if provider.dimensions != 512:
            raise RuntimeError("Configured embedding provider must output 512 dimensions")

        publish_project_status(str(asset.project_id), progress=10, stage="semantic_indexing", message="正在建立素材語意索引")
        db.execute(delete(MediaEmbeddingSegment).where(MediaEmbeddingSegment.media_asset_id == asset.id))
        vectors: list[list[float]] = []
        with tempfile.TemporaryDirectory(prefix=f"semantic-{asset.id}-") as temporary_directory:
            workdir = Path(temporary_directory)
            proxy_path = workdir / "proxy.mp4"
            download_object(asset.proxy_key, str(proxy_path))
            duration = float(asset.duration_seconds or 0)
            if duration <= 0:
                raise RuntimeError("Media duration must be known before embedding")
            for timestamp, frame_path in _extract_keyframes(proxy_path, workdir, duration):
                vector = provider.embed_image(str(frame_path))
                db.add(MediaEmbeddingSegment(
                    media_asset_id=asset.id, modality="keyframe", source_start=timestamp, source_end=timestamp,
                    embedding=vector, metadata_json={"frame_time": timestamp, "provider": provider.name},
                ))
                vectors.append(vector)
                visual = _visual_caption(frame_path, timestamp)
                caption = " | ".join([visual["scene"], ", ".join(visual["objects"]), visual["people_emotion"], visual["action"]]).strip(" |")
                caption_vector = provider.embed_text(caption)
                db.add(MediaEmbeddingSegment(
                    media_asset_id=asset.id, modality="visual_caption", source_start=timestamp, source_end=timestamp,
                    embedding=caption_vector, metadata_json={"text": caption, "visual": visual, "frame_time": timestamp, "provider": provider.name},
                ))
                vectors.append(caption_vector)

            transcript_segments = _latest_transcript_segments(db, asset.id)
            for segment in transcript_segments:
                vector = provider.embed_text(str(segment["text"]))
                db.add(MediaEmbeddingSegment(
                    media_asset_id=asset.id, modality="transcript", source_start=float(segment["start"]),
                    source_end=float(segment["end"]), embedding=vector,
                    metadata_json={"text": segment["text"], "provider": provider.name},
                ))
                vectors.append(vector)

        asset.embedding = _normalised_mean(vectors)
        asset.metadata_json = {**(asset.metadata_json or {}), "semantic_index": {"provider": provider.name, "segment_count": len(vectors), "dominant_scene": "AI 視覺語意索引"}}
        db.commit()
        publish_project_status(str(asset.project_id), progress=100, stage="semantic_index_ready", status="completed", message="素材語意索引完成")
        return {"asset_id": asset_id, "segments": len(vectors), "provider": provider.name}
    except Exception as exc:
        db.rollback()
        if asset is not None:
            publish_project_status(str(asset.project_id), progress=0, stage="semantic_index_failed", status="failed", message=str(exc))
        raise
    finally:
        db.close()
