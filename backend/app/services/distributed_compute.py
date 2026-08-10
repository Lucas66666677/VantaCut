"""Tracker-side immutable planning, signed tickets, consensus, and credit settlement."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import (
    ComputeCreditLedger, ComputeNode, ComputeNodeStatus, DistributedBatchStatus,
    DistributedRenderAssignment, DistributedRenderBatch, DistributedRenderChunk, RenderJob, RenderStatus, User,
)
from app.services.storage import create_upload_url


class DistributedComputeError(RuntimeError):
    pass


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _tracker_secret() -> bytes:
    secret = settings.distributed_compute_tracker_key
    if not secret and settings.environment not in {"development", "test"}:
        raise DistributedComputeError("DISTRIBUTED_COMPUTE_TRACKER_KEY is required outside development")
    return (secret or "development-only-not-for-production").encode("utf-8")


def sign_ticket(payload: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(canonical_json(payload)).rstrip(b"=")
    signature = hmac.new(_tracker_secret(), encoded, hashlib.sha256).digest()
    return f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def verify_ticket(ticket: str) -> dict[str, Any]:
    try:
        raw, signature = ticket.split(".", 1)
        expected = hmac.new(_tracker_secret(), raw.encode("ascii"), hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, actual):
            raise DistributedComputeError("Invalid assignment ticket signature")
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if datetime.fromtimestamp(int(payload["exp"]), UTC) <= datetime.now(UTC):
            raise DistributedComputeError("Assignment ticket expired")
        return payload
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise DistributedComputeError("Malformed assignment ticket") from exc


def verify_node_signature(public_key_b64: str, statement: dict[str, Any], signature_b64: str) -> None:
    """Authenticate a result to its enrolled node; TLS/DTLS alone does not establish account identity."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64), canonical_json(statement))
    except ImportError as exc:
        raise DistributedComputeError("Install cryptography to verify compute-node signatures") from exc
    except Exception as exc:
        raise DistributedComputeError("Invalid compute-node signature") from exc


def _main_segments(confirmed: dict[str, Any]) -> list[dict[str, float]]:
    tracks = confirmed.get("tracks", [])
    raw = next((track.get("clips", []) for track in tracks if track.get("type") == "main_video"), None) if isinstance(tracks, list) else None
    raw = raw if isinstance(raw, list) else confirmed.get("segments", [])
    offset, result = 0.0, []
    for clip in raw if isinstance(raw, list) else []:
        if not isinstance(clip, dict) or clip.get("action", "keep") != "keep":
            continue
        source_start, source_end = float(clip["source_start"]), float(clip["source_end"])
        if source_end <= source_start:
            continue
        result.append({"source_start": source_start, "source_end": source_end, "output_start": offset, "output_end": offset + source_end - source_start})
        offset += source_end - source_start
    return result


def build_chunk_manifests(*, render_job: RenderJob, chunk_seconds: int, replication_factor: int, resolution: str, container_format: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    settings_json = dict(render_job.timeline.settings_json or {})
    confirmed = dict(settings_json.get("confirmed_timeline", {}))
    segments = _main_segments(confirmed)
    if not segments:
        raise DistributedComputeError("Confirmed Timeline has no chunkable main-video segments")
    # Effects depending on neighbouring frames cannot be safely run in isolated chunks.
    forbidden = ("transitions", "parallax", "inpainting", "virtual_relight", "film_optics_master", "subtitles", "audio_description")
    if any(bool(settings_json.get(name, {}).get("enabled", False)) for name in forbidden):
        raise DistributedComputeError("This Timeline contains cross-chunk effects and must use centralized rendering")
    if any(str(dict(settings_json.get(name, {})).get("status", "")) == "completed" for name in ("subtitles", "audio_description")):
        raise DistributedComputeError("Timed subtitle/audio-description assets are not yet represented in the chunk contract")
    if any(track.get("type") != "main_video" for track in confirmed.get("tracks", []) if isinstance(track, dict)):
        raise DistributedComputeError("Multi-track Timeline composition is not yet chunk-safe and must use centralized rendering")
    duration = segments[-1]["output_end"]
    chunks: list[dict[str, Any]] = []
    index, output_start = 0, 0.0
    source_asset_id = str(confirmed.get("source_asset_id", ""))
    while output_start < duration - .001:
        output_end = min(duration, output_start + chunk_seconds)
        pieces: list[dict[str, float]] = []
        for segment in segments:
            left, right = max(output_start, segment["output_start"]), min(output_end, segment["output_end"])
            if right <= left:
                continue
            pieces.append({"source_start": round(segment["source_start"] + left - segment["output_start"], 6), "source_end": round(segment["source_start"] + right - segment["output_start"], 6), "output_start": round(left - output_start, 6), "output_end": round(right - output_start, 6)})
        chunk = {"schema": "com.aivideo.distributed-render.chunk.v1", "chunk_index": index, "output_start_seconds": round(output_start, 6), "output_end_seconds": round(output_end, 6), "source_asset_id": source_asset_id, "pieces": pieces, "render_contract": {"resolution": resolution, "container": container_format, "video_codec": "libx264", "pixel_format": "yuv420p", "ffmpeg_container_digest_required": True, "reset_timestamps": True, "gop_aligned": True}}
        chunk["manifest_sha256"] = sha256_json(chunk)
        chunks.append(chunk); index += 1; output_start = output_end
    batch = {"schema": "com.aivideo.distributed-render.batch.v1", "render_job_id": str(render_job.id), "timeline_id": str(render_job.timeline_id), "project_id": str(render_job.project_id), "chunk_seconds": chunk_seconds, "replication_factor": replication_factor, "chunk_count": len(chunks), "chunks": [{"chunk_index": item["chunk_index"], "manifest_sha256": item["manifest_sha256"]} for item in chunks]}
    batch["manifest_sha256"] = sha256_json(batch)
    return batch, chunks


def create_batch(db: Session, *, render_job: RenderJob, owner_id: UUID, chunk_seconds: int, replication_factor: int, resolution: str, container_format: str) -> DistributedRenderBatch:
    if render_job.project.owner_id != owner_id:
        raise DistributedComputeError("Only the project owner can decentralize this render")
    if db.query(DistributedRenderBatch).filter_by(render_job_id=render_job.id).first():
        raise DistributedComputeError("Render job already has a distributed batch")
    batch_manifest, chunks = build_chunk_manifests(render_job=render_job, chunk_seconds=chunk_seconds, replication_factor=replication_factor, resolution=resolution, container_format=container_format)
    batch = DistributedRenderBatch(render_job_id=render_job.id, project_id=render_job.project_id, owner_id=owner_id, status=DistributedBatchStatus.DISPATCHING, chunk_seconds=chunk_seconds, replication_factor=replication_factor, manifest_json=batch_manifest, manifest_sha256=batch_manifest["manifest_sha256"])
    db.add(batch); db.flush()
    # Prevent a central Celery render from racing this immutable distributed plan.
    render_job.status = RenderStatus.PROCESSING
    render_job.progress = 1
    for item in chunks:
        db.add(DistributedRenderChunk(batch_id=batch.id, chunk_index=item["chunk_index"], output_start_seconds=item["output_start_seconds"], output_end_seconds=item["output_end_seconds"], manifest_json=item, manifest_sha256=item["manifest_sha256"], required_replicas=replication_factor))
    return batch


def _node_can_render(node: ComputeNode, chunk: DistributedRenderChunk) -> bool:
    contract = dict(chunk.manifest_json.get("render_contract", {})); resolution = str(contract.get("resolution", "1080p"))
    required_pixels = {"1080p": 1080, "4k": 2160, "8k": 4320}.get(resolution, 1080)
    if node.status != ComputeNodeStatus.ACTIVE or not bool(dict(node.consent_json).get("explicit_opt_in")):
        return False
    if resolution in {"4k", "8k"} and node.node_kind != "desktop":
        return False
    maximum = int(dict(node.capabilities_json).get("max_resolution", 0))
    if node.node_kind == "browser" and (required_pixels > settings.distributed_compute_max_browser_resolution or maximum < required_pixels):
        return False
    return bool(dict(node.capabilities_json).get("ffmpeg_available", node.node_kind == "desktop"))


def assign_next_chunk(db: Session, node: ComputeNode) -> tuple[DistributedRenderAssignment, str, str] | None:
    now = datetime.now(UTC)
    # Expired tickets simply release the chunk for a fresh, independent node.
    db.query(DistributedRenderAssignment).filter(DistributedRenderAssignment.status == "assigned", DistributedRenderAssignment.expires_at < now).update({"status": "expired"}, synchronize_session=False)
    chunks = db.query(DistributedRenderChunk).filter(DistributedRenderChunk.status.in_(["queued", "assigned"])).order_by(DistributedRenderChunk.created_at).all()
    for chunk in chunks:
        batch = db.get(DistributedRenderBatch, chunk.batch_id)
        if batch is None or batch.owner_id == node.owner_id or not _node_can_render(node, chunk):
            continue
        existing = db.query(DistributedRenderAssignment).filter_by(chunk_id=chunk.id, node_id=node.id).first()
        active_count = db.query(DistributedRenderAssignment).filter(DistributedRenderAssignment.chunk_id == chunk.id, DistributedRenderAssignment.status.in_(["assigned", "submitted", "verified"])).count()
        if existing or active_count >= chunk.required_replicas:
            continue
        nonce, expiry = secrets.token_urlsafe(32), now + timedelta(seconds=settings.distributed_compute_ticket_ttl_seconds)
        assignment = DistributedRenderAssignment(chunk_id=chunk.id, node_id=node.id, ticket_nonce=nonce, ticket_sha256="pending", expires_at=expiry)
        db.add(assignment); db.flush()
        payload = {"assignment_id": str(assignment.id), "batch_id": str(batch.id), "chunk_id": str(chunk.id), "node_id": str(node.id), "manifest_sha256": chunk.manifest_sha256, "nonce": nonce, "exp": int(expiry.timestamp())}
        ticket = sign_ticket(payload); assignment.ticket_sha256 = hashlib.sha256(ticket.encode("utf-8")).hexdigest(); chunk.status = "assigned"
        key = f"distributed-renders/{batch.id}/chunks/{chunk.chunk_index:06d}/attempts/{assignment.id}.mp4"
        return assignment, ticket, key
    return None


def settle_credits(db: Session, assignments: list[DistributedRenderAssignment], checksum: str) -> None:
    for assignment in assignments:
        if db.query(ComputeCreditLedger).filter_by(idempotency_key=f"verified-chunk:{assignment.id}").first():
            continue
        node = db.get(ComputeNode, assignment.node_id); user = db.get(User, node.owner_id) if node else None
        if not node or not user:
            continue
        amount = settings.distributed_compute_credit_per_verified_chunk
        db.add(ComputeCreditLedger(user_id=user.id, node_id=node.id, assignment_id=assignment.id, amount=amount, event_type="verified_chunk", idempotency_key=f"verified-chunk:{assignment.id}", metadata_json={"checksum": checksum}))
        user.render_credits += amount; node.reputation_score += 1


def resolve_consensus(db: Session, chunk: DistributedRenderChunk) -> bool:
    verified = db.query(DistributedRenderAssignment).filter_by(chunk_id=chunk.id, status="verified").all()
    groups: dict[str, list[DistributedRenderAssignment]] = {}
    for item in verified:
        if item.output_checksum:
            groups.setdefault(item.output_checksum, []).append(item)
    winner = next(((checksum, items) for checksum, items in groups.items() if len(items) >= chunk.required_replicas), None)
    if winner is None:
        if len(verified) >= chunk.required_replicas:
            for item in verified: item.status = "disputed"
            chunk.status = "queued"
        return False
    checksum, assignments = winner
    chunk.status, chunk.accepted_checksum, chunk.accepted_object_key = "accepted", checksum, assignments[0].output_object_key
    for item in assignments: item.status = "accepted"
    settle_credits(db, assignments, checksum)
    return True
