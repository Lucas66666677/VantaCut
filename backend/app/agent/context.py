from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Iterable

from app.models.entities import MediaAsset, Timeline


MAX_CONTEXT_CLIPS = 120
MAX_CONTEXT_ASSETS = 40


def _number(value: Any) -> float | Any:
    return float(value) if isinstance(value, Decimal) else value


def build_timeline_state_context(timeline: Timeline, assets: Iterable[MediaAsset]) -> dict[str, Any]:
    """Create a compact, ID-grounded state snapshot rather than passing an unbounded project dump."""
    clips = sorted(timeline.clips, key=lambda item: (item.track.value, item.order_index))[:MAX_CONTEXT_CLIPS]
    layout = dict((timeline.settings_json or {}).get("clip_layout", {}))
    audio_mixes = dict((timeline.settings_json or {}).get("clip_audio_mixes", {}))
    settings = timeline.settings_json or {}
    transcript_index = list(settings.get("transcript_index", []))
    if not transcript_index:
        transcript_index = [
            {
                "start_time": item.get("start_time"), "end_time": item.get("end_time"),
                "text": item.get("text", ""),
            }
            for item in dict(settings.get("subtitles", {})).get("items", [])
            if isinstance(item, dict)
        ]
    return {
        "timeline": {
            "id": str(timeline.id),
            "version": timeline.version,
            "parent_timeline_id": str(timeline.parent_timeline_id) if timeline.parent_timeline_id else None,
            "is_current": timeline.is_current,
        },
        "clips": [
            {
                "clip_id": str(clip.id),
                "source_asset_id": str(clip.source_asset_id),
                "source_start": _number(clip.source_start),
                "source_end": _number(clip.source_end),
                "track": clip.track.value,
                "enabled": clip.enabled,
                "audio_enabled": clip.audio_enabled,
                "audio_effects": clip.audio_effects,
                "timeline_start": layout.get(str(clip.id), {}).get("timeline_start"),
                "gain_db": audio_mixes.get(str(clip.id), {}).get("gain_db", 0),
            }
            for clip in clips
        ],
        "available_b_roll_assets": [
            {
                "source_asset_id": str(asset.id),
                "filename": asset.filename,
                "duration_seconds": _number(asset.duration_seconds),
                "semantic_tags": list((asset.metadata_json or {}).get("semantic_tags", []))[:8],
            }
            for asset in list(assets)[:MAX_CONTEXT_ASSETS]
            if asset.status.value == "ready" and asset.media_type.value == "video"
        ],
        "approved_lut_keys": list(settings.get("approved_lut_keys", []))[:20],
        "transcript_index": transcript_index[:80],
        "context_limits": {"clips": MAX_CONTEXT_CLIPS, "assets": MAX_CONTEXT_ASSETS},
    }


def serialise_timeline_state_context(timeline: Timeline, assets: Iterable[MediaAsset]) -> str:
    return json.dumps(build_timeline_state_context(timeline, assets), ensure_ascii=False, separators=(",", ":"))
