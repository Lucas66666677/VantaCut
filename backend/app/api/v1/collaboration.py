from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.collaboration import collaboration_hub


router = APIRouter(prefix="/timelines", tags=["collaboration"])


@router.websocket("/{timeline_id}/collaboration")
async def collaborate_on_timeline(websocket: WebSocket, timeline_id: str) -> None:
    """Relay binary Yjs updates and JSON presence messages for one timeline room.

    Production authentication should validate the user's project access before accept.
    """
    await websocket.accept()
    await collaboration_hub.join(timeline_id, websocket)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await collaboration_hub.publish_update(timeline_id, message["bytes"])
            elif message.get("text") is not None:
                await collaboration_hub.publish_presence(timeline_id, message["text"])
    except WebSocketDisconnect:
        pass
    finally:
        await collaboration_hub.leave(timeline_id, websocket)
