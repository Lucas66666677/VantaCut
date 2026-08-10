"""Independent verifier and central concat stage for community-rendered chunks."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import DistributedBatchStatus, DistributedRenderAssignment, DistributedRenderBatch, DistributedRenderChunk, RenderStatus
from app.services.distributed_compute import resolve_consensus, verify_node_signature
from app.services.storage import download_object, upload_object
from app.worker import celery_app


class DistributedVerificationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _decoded_fingerprint(path: Path) -> str:
    """FrameMD5 catches a valid container with altered/corrupt decoded media."""
    try:
        completed = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a?", "-f", "framemd5", "-"], capture_output=True, timeout=20 * 60)
    except subprocess.TimeoutExpired as exc:
        raise DistributedVerificationError("Decoded checksum timed out") from exc
    if completed.returncode:
        raise DistributedVerificationError(f"FFmpeg decoded checksum failed: {completed.stderr.decode(errors='replace')[-1200:]}")
    return hashlib.sha256(completed.stdout).hexdigest()


def _duration(path: Path) -> float:
    completed = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if completed.returncode:
        raise DistributedVerificationError(completed.stderr[-1000:])
    return float(json.loads(completed.stdout)["format"]["duration"])


@celery_app.task(bind=True, name="compute.verify_chunk_result")
def verify_chunk_result(self, assignment_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        assignment = db.get(DistributedRenderAssignment, UUID(assignment_id))
        if assignment is None or assignment.status != "submitted" or not assignment.output_object_key:
            raise DistributedVerificationError("Submitted assignment not found")
        chunk, node = assignment.chunk, assignment.node
        statement = {"assignment_id": str(assignment.id), "chunk_id": str(chunk.id), "output_object_key": assignment.output_object_key, "output_sha256": assignment.output_checksum, "decoded_fingerprint": assignment.decoded_fingerprint, "renderer_image_digest": assignment.renderer_image_digest}
        if not assignment.node_signature:
            raise DistributedVerificationError("Missing node signature")
        verify_node_signature(node.public_key, statement, assignment.node_signature)
        with tempfile.TemporaryDirectory(prefix=f"verify-chunk-{assignment.id}-") as temporary:
            output = Path(temporary) / "chunk.mp4"; download_object(assignment.output_object_key, str(output))
            if _sha256(output) != assignment.output_checksum:
                raise DistributedVerificationError("Returned object SHA-256 does not match node claim")
            declared_fingerprint = str(assignment.decoded_fingerprint or "")
            if _decoded_fingerprint(output) != declared_fingerprint:
                raise DistributedVerificationError("Returned decoded media fingerprint does not match node claim")
            expected_duration = float(chunk.output_end_seconds) - float(chunk.output_start_seconds)
            if abs(_duration(output) - expected_duration) > .20:
                raise DistributedVerificationError("Chunk duration differs from signed render contract")
        assignment.status = "verified"
        accepted = resolve_consensus(db, chunk); db.commit()
        batch = chunk.batch
        if accepted and all(item.status == "accepted" for item in db.query(DistributedRenderChunk).filter_by(batch_id=batch.id).all()):
            assemble_distributed_batch.delay(str(batch.id))
        return {"assignment_id": assignment_id, "status": assignment.status, "consensus_accepted": accepted}
    except Exception as exc:
        db.rollback()
        assignment = db.get(DistributedRenderAssignment, UUID(assignment_id))
        if assignment is not None:
            assignment.status = "rejected"; assignment.error_message = str(exc); db.commit()
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="compute.assemble_distributed_batch")
def assemble_distributed_batch(self, batch_id: str) -> dict[str, Any]:
    db = SessionLocal(); batch: DistributedRenderBatch | None = None
    try:
        batch = db.get(DistributedRenderBatch, UUID(batch_id))
        if batch is None:
            raise DistributedVerificationError("Distributed batch not found")
        chunks = db.query(DistributedRenderChunk).filter_by(batch_id=batch.id).order_by(DistributedRenderChunk.chunk_index).all()
        if not chunks or any(item.status != "accepted" or not item.accepted_object_key for item in chunks):
            raise DistributedVerificationError("Cannot assemble before every chunk reaches checksum consensus")
        batch.status = DistributedBatchStatus.ASSEMBLING; db.commit()
        publish_project_status(str(batch.project_id), progress=96, stage="distributed_concat", message="正在無損拼接已共識驗證的分散式區塊", job_id=str(batch.render_job_id))
        with tempfile.TemporaryDirectory(prefix=f"distributed-concat-{batch.id}-") as temporary:
            workdir = Path(temporary); paths: list[Path] = []
            for item in chunks:
                path = workdir / f"{item.chunk_index:06d}.mp4"; download_object(str(item.accepted_object_key), str(path)); paths.append(path)
            listing = workdir / "concat.txt"
            listing.write_text("".join(f"file '{path.as_posix()}'\n" for path in paths), encoding="utf-8")
            output = workdir / "final.mp4"
            completed = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(output)], capture_output=True, text=True, timeout=4 * 60 * 60)
            if completed.returncode:
                raise DistributedVerificationError(f"Lossless chunk concat failed: {completed.stderr[-2400:]}")
            output_key = f"projects/{batch.project_id}/renders/{batch.render_job_id}/distributed-final.mp4"; upload_object(output_key, str(output), "video/mp4")
        batch.status, batch.final_output_key = DistributedBatchStatus.COMPLETED, output_key
        job = batch.render_job; job.status, job.progress, job.output_key, job.output_format, job.error_message = RenderStatus.COMPLETED, 100, output_key, "mp4", None
        db.commit(); publish_project_status(str(batch.project_id), progress=100, stage="distributed_render_completed", status="completed", message="去中心化渲染與無損拼接完成", job_id=str(job.id))
        return {"batch_id": batch_id, "output_key": output_key, "status": "completed"}
    except Exception as exc:
        db.rollback()
        if batch is not None:
            batch.status, batch.error_message = DistributedBatchStatus.FAILED, str(exc); db.commit()
            publish_project_status(str(batch.project_id), progress=0, stage="distributed_render_failed", status="failed", message=str(exc), job_id=str(batch.render_job_id))
        raise
    finally:
        db.close()
