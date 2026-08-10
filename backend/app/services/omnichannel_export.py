"""Renderer-neutral plans and live progress for the Omnichannel Export Matrix."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

import redis

from app.core.config import settings


@dataclass(frozen=True)
class MatrixProfile:
    key: str
    aspect_ratio: str
    label: str
    auto_reframe: bool


MATRIX_PROFILES: tuple[MatrixProfile, ...] = (
    MatrixProfile("landscape", "16:9", "橫式 16:9", False),
    MatrixProfile("vertical", "9:16", "直式 9:16", True),
    MatrixProfile("square", "1:1", "方形 1:1", True),
)


def build_virtual_timelines(confirmed_timeline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Create isolated Timeline documents; no user Timeline or Clip rows are mutated.

    The documents travel with individual Celery messages, so Kubernetes workers
    may render the same source independently without sharing a local filesystem.
    """
    if not confirmed_timeline:
        raise ValueError("Confirmed timeline is required for matrix export")
    variants: dict[str, dict[str, Any]] = {}
    for profile in MATRIX_PROFILES:
        document = copy.deepcopy(confirmed_timeline)
        document["virtual_canvas"] = {
            "kind": "omnichannel_virtual_timeline", "profile": profile.key,
            "aspect_ratio": profile.aspect_ratio, "auto_reframe": profile.auto_reframe,
        }
        variants[profile.key] = document
    return variants


def _progress_key(batch_id: str) -> str:
    return f"omnichannel-export:{batch_id}:progress"


def publish_matrix_variant_progress(
    *, batch_id: str | None, variant: str | None, progress: int, status: str, message: str,
) -> None:
    """Best-effort live updates. Persistent RenderJob rows remain the source of truth."""
    if not batch_id or not variant:
        return
    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        raw = client.get(_progress_key(batch_id)) or "{}"
        payload = json.loads(raw)
        payload[variant] = {"progress": max(0, min(100, int(progress))), "status": status, "message": message}
        client.setex(_progress_key(batch_id), 60 * 60 * 24, json.dumps(payload, ensure_ascii=False))
        client.close()
    except Exception:
        # Never fail a costly render because the transient progress channel is unavailable.
        return


def matrix_progress(batch_id: str) -> dict[str, dict[str, Any]]:
    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        raw = client.get(_progress_key(batch_id)) or "{}"; client.close()
        return json.loads(raw)
    except Exception:
        return {}
