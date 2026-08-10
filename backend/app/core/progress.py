import json
import os
from datetime import datetime, timezone
from typing import Any

from redis import Redis


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STATUS_TTL_SECONDS = 60 * 60 * 24


def project_status_key(project_id: str) -> str:
    return f"project:{project_id}:status"


def project_status_channel(project_id: str) -> str:
    return f"project:{project_id}:status-events"


def publish_project_status(
    project_id: str,
    *,
    progress: int,
    stage: str,
    status: str = "processing",
    message: str | None = None,
    job_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist the latest project status and publish it for SSE consumers."""
    payload: dict[str, Any] = {
        "project_id": project_id,
        "progress": max(0, min(100, int(progress))),
        "stage": stage,
        "status": status,
        "message": message,
        "job_id": job_id,
        "extra": extra or {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    client = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.setex(project_status_key(project_id), STATUS_TTL_SECONDS, encoded)
        client.publish(project_status_channel(project_id), encoded)
    finally:
        client.close()
    return payload
