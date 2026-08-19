"""Opt-in compute tracker and WebRTC signaling endpoints."""
from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import ComputeNode, ComputeNodeStatus, DistributedRenderAssignment, RenderJob, RenderStatus, User
from app.schemas.distributed_compute import (
    AssignmentResponse, AssignmentResultRequest, ComputeNodeEnrollRequest, ComputeNodeHeartbeatRequest,
    ComputeNodeResponse, DecentralizeRenderRequest, DistributedBatchResponse,
)
from app.services.compute_signaling import compute_signaling_hub
from app.services.distributed_compute import (
    DistributedComputeError, assign_next_chunk, create_batch, verify_node_signature, verify_ticket,
)
from app.services.storage import create_upload_url
from app.tasks.distributed_compute_tasks import verify_chunk_result


router = APIRouter(prefix="/compute", tags=["distributed-compute"])


def _node_credits(db: Session, node: ComputeNode) -> int:
    from sqlalchemy import func
    from app.models.entities import ComputeCreditLedger
    return int(db.query(func.coalesce(func.sum(ComputeCreditLedger.amount), 0)).filter_by(node_id=node.id).scalar() or 0)


@router.post("/nodes", response_model=ComputeNodeResponse, status_code=status.HTTP_201_CREATED)
def enroll_compute_node(
    payload: ComputeNodeEnrollRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ComputeNodeResponse:
    # This is an END-USER enrollment action (a person opting their own
    # browser/desktop into the compute pool), not part of the compute-node
    # protocol itself: the node's own identity/authorization for every
    # subsequent call (heartbeat, assignments, signed results) is the
    # Ed25519 keypair verified by verify_node_signature below, which this
    # change does not touch. Only "which user does this enrollment belong
    # to" moves from a client-supplied owner_id to the verified caller.
    try:
        if len(base64.b64decode(payload.public_key, validate=True)) != 32:
            raise ValueError
    except Exception as exc:
        raise HTTPException(status_code=422, detail="public_key must be a base64 Ed25519 public key") from exc
    existing = db.query(ComputeNode).filter_by(public_key=payload.public_key).first()
    if existing:
        if existing.owner_id != current_user.id:
            raise HTTPException(status_code=409, detail="This compute identity is already registered to another user")
        return ComputeNodeResponse(node_id=existing.id, status=existing.status.value, credits_earned=_node_credits(db, existing))
    node = ComputeNode(owner_id=current_user.id, label=payload.label, public_key=payload.public_key, node_kind=payload.node_kind, status=ComputeNodeStatus.ACTIVE, capabilities_json=payload.capabilities, consent_json=payload.consent, renderer_image_digest=payload.renderer_image_digest, last_heartbeat_at=datetime.now(UTC))
    db.add(node); db.commit(); db.refresh(node)
    return ComputeNodeResponse(node_id=node.id, status=node.status.value)


@router.post("/nodes/{node_id}/heartbeat", response_model=ComputeNodeResponse)
def node_heartbeat(node_id: UUID, payload: ComputeNodeHeartbeatRequest, db: Session = Depends(get_db)) -> ComputeNodeResponse:
    node = db.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Compute node not found")
    statement = {"node_id": str(node.id), "available": payload.available, "capabilities": payload.capabilities, "signed_at": payload.signed_at.isoformat()}
    try:
        verify_node_signature(node.public_key, statement, payload.signature)
    except DistributedComputeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    node.capabilities_json, node.last_heartbeat_at = payload.capabilities, datetime.now(UTC)
    node.status = ComputeNodeStatus.ACTIVE if payload.available else ComputeNodeStatus.PAUSED
    db.commit()
    return ComputeNodeResponse(node_id=node.id, status=node.status.value, credits_earned=_node_credits(db, node))


@router.post("/render-jobs/{render_job_id}/offload", response_model=DistributedBatchResponse, status_code=status.HTTP_202_ACCEPTED)
def offload_render_job(render_job_id: UUID, payload: DecentralizeRenderRequest, db: Session = Depends(get_db)) -> DistributedBatchResponse:
    job = db.get(RenderJob, render_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found")
    if job.status != RenderStatus.QUEUED:
        raise HTTPException(status_code=409, detail="Only a queued render job can be moved to the distributed pool")
    try:
        batch = create_batch(db, render_job=job, owner_id=payload.owner_id, chunk_seconds=payload.chunk_seconds, replication_factor=payload.replication_factor, resolution=payload.resolution, container_format=payload.container_format)
        db.commit(); db.refresh(batch)
        return DistributedBatchResponse(batch_id=batch.id, status=batch.status.value, chunk_count=int(batch.manifest_json["chunk_count"]), manifest_sha256=batch.manifest_sha256)
    except DistributedComputeError as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/nodes/{node_id}/assignments/next", response_model=AssignmentResponse)
def next_assignment(node_id: UUID, db: Session = Depends(get_db)) -> AssignmentResponse:
    node = db.get(ComputeNode, node_id)
    if node is None or node.status != ComputeNodeStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Node is not available for compute")
    result = assign_next_chunk(db, node)
    if result is None:
        db.commit(); return AssignmentResponse(status="idle")
    assignment, ticket, result_key = result; db.commit()
    return AssignmentResponse(assignment_id=assignment.id, status="assigned", ticket=ticket, manifest=assignment.chunk.manifest_json, result_upload_key=result_key, result_upload_url=create_upload_url(result_key, "video/mp4"), expires_at=assignment.expires_at)


@router.post("/nodes/{node_id}/assignments/{assignment_id}/result", status_code=status.HTTP_202_ACCEPTED)
def submit_assignment_result(node_id: UUID, assignment_id: UUID, payload: AssignmentResultRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    assignment = db.get(DistributedRenderAssignment, assignment_id)
    if assignment is None or assignment.node_id != node_id or assignment.status != "assigned":
        raise HTTPException(status_code=404, detail="Active assignment not found")
    try:
        ticket = verify_ticket(payload.assignment_ticket)
        if str(ticket.get("assignment_id")) != str(assignment.id) or str(ticket.get("node_id")) != str(node_id) or hashlib.sha256(payload.assignment_ticket.encode("utf-8")).hexdigest() != assignment.ticket_sha256:
            raise DistributedComputeError("Ticket does not belong to this assignment")
        prefix = f"distributed-renders/{assignment.chunk.batch_id}/chunks/{assignment.chunk.chunk_index:06d}/attempts/{assignment.id}."
        if not payload.output_object_key.startswith(prefix):
            raise DistributedComputeError("Result key is outside this assignment namespace")
        if assignment.node.renderer_image_digest and assignment.node.renderer_image_digest != payload.renderer_image_digest:
            raise DistributedComputeError("Renderer image digest differs from enrolled deterministic runtime")
        statement = {"assignment_id": str(assignment.id), "chunk_id": str(assignment.chunk_id), "output_object_key": payload.output_object_key, "output_sha256": payload.output_sha256, "decoded_fingerprint": payload.decoded_fingerprint, "renderer_image_digest": payload.renderer_image_digest}
        verify_node_signature(assignment.node.public_key, statement, payload.signature)
    except DistributedComputeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    assignment.output_object_key, assignment.output_checksum, assignment.decoded_fingerprint, assignment.renderer_image_digest, assignment.node_signature, assignment.status = payload.output_object_key, payload.output_sha256, payload.decoded_fingerprint, payload.renderer_image_digest, payload.signature, "submitted"
    db.commit(); task = verify_chunk_result.delay(str(assignment.id))
    return {"assignment_id": str(assignment.id), "verification_task_id": task.id, "status": "submitted"}


@router.websocket("/nodes/{node_id}/signal")
async def relay_compute_signaling(websocket: WebSocket, node_id: str) -> None:
    """Only SDP/ICE/encrypted-session metadata is relayed; chunk bytes use RTCDataChannel."""
    await websocket.accept(); await compute_signaling_hub.join(node_id, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            target = str(message.get("target_node_id", ""))
            if not target or not isinstance(message.get("signal"), dict):
                await websocket.send_json({"type": "error", "detail": "target_node_id and signal are required"}); continue
            await compute_signaling_hub.relay(target, __import__("json").dumps({"from_node_id": node_id, "signal": message["signal"]}))
    except WebSocketDisconnect:
        pass
    finally:
        await compute_signaling_hub.leave(node_id, websocket)
