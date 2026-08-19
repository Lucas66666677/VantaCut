from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.auth.websocket import authenticate_websocket_bearer
from app.db.session import get_db
from app.models.entities import Timeline, User
from app.services.collaboration import collaboration_hub

router = APIRouter(prefix="/timelines", tags=["collaboration"])


def _authorize_timeline_access(db: Session, timeline_id: UUID, user: User) -> Timeline | None:
    """Real-time collaboration access — owner-only, corrected from an earlier,
    overbroad "owner OR ReviewParticipant" version of this function.

    This codebase has no dedicated "collaboration" permission table and no
    general project-membership/workspace model — `Project.owner_id` is the
    only relation in the codebase that is ever evidenced to grant
    *editing* rights. `ReviewParticipant` (see app/models/entities.py) is
    the only non-owner grant that exists at all, but a focused trace of
    every route it actually gates — app/api/v1/reviews.py's
    `_timeline_for_user`, used by list/create/update *review comments*
    and the approve/reject *decision* endpoint — shows it is scoped
    exclusively to commentary and review decisions on the separate
    `ReviewComment`/`TimelineReview` entities. No reviews.py route ever
    reads or writes `Timeline.settings_json` or a `Clip` row.

    This WebSocket is not that: it relays raw binary Yjs CRDT updates
    (see app/services/collaboration.py's `CollaborationHub` and
    frontend/features/editor/use-collaborative-timeline.ts) that directly
    mutate the shared `clips` Y.Map backing the actual editable timeline —
    `updateClip()` writes local edits into that map, and every joined
    peer applies every other peer's binary updates directly into their own
    copy of the real timeline content. Joining this socket is therefore
    authoritative live-edit access, not comment/read access. Reusing
    ReviewParticipant here — a grant with no evidenced editing scope
    anywhere else in the codebase — would extend it beyond what it is
    actually proven to mean, which is inventing new permission semantics
    by a different name, not reusing an existing one. A second focused
    check confirmed no other model (e.g. `WorkspacePreference`, which is
    just a user's own per-project UI layout state, not a grant to anyone
    else) grants a non-owner editing rights on a timeline anywhere in this
    codebase. So the only existing model that actually grants editing is
    ownership, and this function is narrowed to exactly that.

    Whether reviewers/approvers should get some form of live collaboration
    access (e.g. a genuinely read-only "spectator" presence-only mode, or a
    deliberately-designed extension of ReviewParticipant's scope) is a real,
    open product question — recorded as a follow-up for the owner, not
    guessed at here.
    """
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        return None
    if timeline.project.owner_id != user.id:
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
    new client) — see `_authorize_timeline_access` for the access model
    (project owner only; see that function's docstring for why a broader
    ReviewParticipant-based grant was considered and rejected).
    Reuses the same `Sec-WebSocket-Protocol` bearer convention as
    project_status.py's WebSocket, via the shared
    app.auth.websocket.authenticate_websocket_bearer helper (browsers
    cannot set a custom Authorization header on the WebSocket constructor);
    the token is never logged.
    """
    user = await authenticate_websocket_bearer(websocket, db)
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
