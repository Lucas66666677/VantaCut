"""WebSocket relay for WebRTC offer/answer/ICE only; media bytes never traverse this hub."""
from __future__ import annotations

from collections import defaultdict
from fastapi import WebSocket


class ComputeSignalingHub:
    def __init__(self) -> None:
        self._sockets: dict[str, set[WebSocket]] = defaultdict(set)

    async def join(self, node_id: str, socket: WebSocket) -> None:
        self._sockets[node_id].add(socket)

    async def leave(self, node_id: str, socket: WebSocket) -> None:
        self._sockets[node_id].discard(socket)
        if not self._sockets[node_id]:
            self._sockets.pop(node_id, None)

    async def relay(self, target_node_id: str, message: str) -> None:
        for socket in list(self._sockets.get(target_node_id, ())):
            await socket.send_text(message)


compute_signaling_hub = ComputeSignalingHub()
