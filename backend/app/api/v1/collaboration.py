from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.project_status import _authenticate_websocket
from app.db.session import get_db
from app.models.entities import ReviewParticipant, Timeline, User
from app.services.collaboration import collaboration_hub

router = APIRouter(prefix="/timelines", tags=["collaboration"])


def _authorize_timeline_access(db: Session, timeline_id: UUID, user: User) -> Timeline | None:
    """Real-time collaboration access, evidence-based rather than invented.

    This codebase has no dedicated "collaboration" permission table and no
    general project-membership/workspace model — `Project.owner_id` is the
    only ownership relation on Project. The one place a non-owner is ever
    legitimately granted access to a *specific timeline* is
    `ReviewParticipant` (see app/models/entities.py, and the existing
    authorization helper this mirrors: app/api/v1/reviews.py's
    `_timeline_for_user`, which already treats "project owner OR a
    ReviewParticipant row for this timeline" as the timeline's access list
    for review comments/decisions). Reusing that exact model here — rather
    than restricting collaboration to the owner alone — is what keeps this
    fix from silently breaking real multi-user review/edit workflows the
    product already supports; inventing a stricter or looser model than
    reviews.py's would be a guess this session isn't in a position to make.

    Known residual nuance, documented rather than silently resolved: a
    ReviewParticipant with the "reviewer" role gets the same live-editing
    (Yjs update) access as "approver" here, because this WebSocket relays a
    single undifferentiated event stream (binary Yjs ops + JSON presence)
    with no read/write split — reviews.py itself only distinguishes
    reviewer vs. approver for the *decision* endpoint, not for read access.
    Splitting collaboration into read-only vs. edit-capable participant
    roles would be new product semantics, not a reuse of an existing one,
    so it is out of scope here and left as a follow-up.
    """
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        return None
    if timeline.project.owner_id == user.id:
        return timeline
    participant = db.scalar(
        select(ReviewParticipant).where(
            ReviewParticipant.timeline_id == timeline.id,
            ReviewParticipant.user_id == user.id,
        )
    )
    if participant is None:
        return None
    return timeline


@router.websocket("/{timeline_id}/collaboration")
async def collaborate_on_timeline(
    websocket: WebSocket,
    timeline_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    """Relay binary Yjs updates and JSON presence messages for one timeline room.

    Authentication and authorization both happen BEFORE `websocket.accept()`
    and before the caller ever joins a `collaboration_hub` room (which
    starts a Redis pub/sub subscription and replays buffered updates to the
    new client) — see `_authorize_timeline_access` for the access model.
    Reuses the same `Sec-WebSocket-Protocol` bearer convention as
    project_status.py's WebSocket (browsers cannot set a custom
    Authorization header on the WebSocket constructor); the token is never
    logged.
    """
    user = await _authenticate_websocket(websocket, db)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    timeline = _authorize_timeline_access(db, timeline_id, user)
    if timeline is None:
        # Same close code, no message, whether the timeline doesn't exist or
        # simply isn't accessible to this user — do not confirm the
        # existence of another user's private timeline to an unauthorized
        # caller.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept(subprotocol="bearer")
    await collaboration_hub.join(str(timeline_id), websocket)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await collaboration_hub.publish_update(str(timeline_id), message["bytes"])
            elif message.get("text") is not None:
                await collaboration_hub.publish_presence(str(timeline_id), message["text"])
    except WebSocketDisconnect:
        pass
    finally:
        await collaboration_hub.leave(str(timeline_id), websocket)
