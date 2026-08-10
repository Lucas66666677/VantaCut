"""Transactional application of constrained Agent edit plans to immutable Timeline versions."""
from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.editing_tools import (
    AddBGMInput,
    AdjustAudioLevelInput,
    ApplyLUTInput,
    InsertBRollInput,
    PlannedToolCall,
    TrimClipInput,
)
from app.models.entities import Clip, MediaAsset, Timeline, TrackType


class AgentToolApplicationError(ValueError):
    pass


def _replace_clip_ids(value: Any, id_map: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_clip_ids(item, id_map) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_clip_ids(item, id_map) for item in value]
    return id_map.get(str(value), value)


def clone_timeline_version(db: Session, source: Timeline, *, label: str) -> Timeline:
    """Copy relational clips and JSON settings into a non-current child version."""
    max_version = db.scalar(select(func.max(Timeline.version)).where(Timeline.project_id == source.project_id)) or 0
    target = Timeline(
        project_id=source.project_id,
        name=label[:200],
        version=int(max_version) + 1,
        is_current=False,
        parent_timeline_id=source.id,
        settings_json=copy.deepcopy(source.settings_json or {}),
    )
    db.add(target)
    db.flush()
    copied: list[tuple[Clip, Clip]] = []
    for source_clip in source.clips:
        target_clip = Clip(
            timeline_id=target.id,
            source_asset_id=source_clip.source_asset_id,
            source_start=source_clip.source_start,
            source_end=source_clip.source_end,
            track=source_clip.track,
            z_index=source_clip.z_index,
            audio_enabled=source_clip.audio_enabled,
            audio_effects=list(source_clip.audio_effects or []),
            order_index=source_clip.order_index,
            enabled=source_clip.enabled,
        )
        db.add(target_clip)
        copied.append((source_clip, target_clip))
    db.flush()
    target.settings_json = _replace_clip_ids(
        target.settings_json, {str(old.id): str(new.id) for old, new in copied}
    )
    return target


def _clip_or_error(timeline: Timeline, clip_id: UUID) -> Clip:
    for clip in timeline.clips:
        if clip.id == clip_id:
            return clip
    raise AgentToolApplicationError("Tool call references a clip outside the source Timeline")


def _approved_lut_keys(timeline: Timeline) -> set[str]:
    settings = timeline.settings_json or {}
    approved = set(settings.get("approved_lut_keys", []))
    if existing := dict(settings.get("color_lut", {})).get("lut_key"):
        approved.add(existing)
    return approved


def apply_planned_tool_calls(db: Session, timeline: Timeline, calls: list[PlannedToolCall]) -> None:
    """Apply all operations to the child Timeline only; caller owns the transaction."""
    settings = copy.deepcopy(timeline.settings_json or {})
    layout = dict(settings.get("clip_layout", {}))
    audio_mixes = dict(settings.get("clip_audio_mixes", {}))
    for call in calls:
        args = call.validated_arguments()
        if isinstance(args, TrimClipInput):
            clip = _clip_or_error(timeline, args.clip_id)
            clip.source_start, clip.source_end = args.source_start, args.source_end
        elif isinstance(args, InsertBRollInput):
            asset = db.get(MediaAsset, args.source_asset_id)
            if asset is None or asset.project_id != timeline.project_id or asset.media_type.value != "video":
                raise AgentToolApplicationError("B-Roll asset is not an approved video in this project")
            if asset.duration_seconds is not None and args.source_end > float(asset.duration_seconds):
                raise AgentToolApplicationError("B-Roll source_end exceeds the asset duration")
            clip = Clip(
                timeline=timeline, source_asset_id=args.source_asset_id,
                source_start=args.source_start, source_end=args.source_end,
                track=TrackType.B_ROLL, z_index=args.z_index, audio_enabled=False,
                audio_effects=[], order_index=max((item.order_index for item in timeline.clips), default=-1) + 1,
                enabled=True,
            )
            db.add(clip)
            db.flush()
            layout[str(clip.id)] = {"timeline_start": args.timeline_start}
        elif isinstance(args, AdjustAudioLevelInput):
            _clip_or_error(timeline, args.clip_id)
            audio_mixes[str(args.clip_id)] = {"gain_db": args.gain_db}
        elif isinstance(args, ApplyLUTInput):
            if args.lut_key not in _approved_lut_keys(timeline):
                raise AgentToolApplicationError("LUT key is not approved for this Timeline")
            settings["color_lut"] = {"lut_key": args.lut_key, "intensity": args.intensity}
        elif isinstance(args, AddBGMInput):
            # This remains an intent until the generated-music workflow is explicitly approved.
            settings["agent_bgm_request"] = {"mood": args.mood, "mix_level": args.mix_level}
        else:
            raise AgentToolApplicationError(f"Unsupported tool input: {type(args).__name__}")
    settings["clip_layout"] = layout
    settings["clip_audio_mixes"] = audio_mixes
    timeline.settings_json = settings
    db.flush()
    materialise_confirmed_timeline(timeline)


def materialise_confirmed_timeline(timeline: Timeline) -> None:
    """Keep render/export JSON synchronised with relational Clip data after an Agent edit."""
    settings = copy.deepcopy(timeline.settings_json or {})
    layout = dict(settings.get("clip_layout", {}))
    audio_mixes = dict(settings.get("clip_audio_mixes", {}))
    grouped: dict[TrackType, list[Clip]] = {}
    for clip in sorted(timeline.clips, key=lambda item: (item.track.value, item.z_index, item.order_index)):
        grouped.setdefault(clip.track, []).append(clip)
    tracks: list[dict[str, Any]] = []
    main_cursor = 0.0
    for track_type, clips in grouped.items():
        document_clips: list[dict[str, Any]] = []
        for clip in clips:
            duration = float(clip.source_end) - float(clip.source_start)
            timeline_start = main_cursor if track_type == TrackType.MAIN_VIDEO else float(layout.get(str(clip.id), {}).get("timeline_start", 0))
            if track_type == TrackType.MAIN_VIDEO and clip.enabled:
                main_cursor += duration
            document_clips.append({
                "id": str(clip.id), "source_asset_id": str(clip.source_asset_id),
                "source_start": float(clip.source_start), "source_end": float(clip.source_end),
                "timeline_start": timeline_start, "action": "keep" if clip.enabled else "remove",
                "z_index": clip.z_index, "audio_enabled": clip.audio_enabled,
                "audio_effects": list(clip.audio_effects or []),
                "gain_db": float(audio_mixes.get(str(clip.id), {}).get("gain_db", 0)),
            })
        tracks.append({"type": track_type.value, "z_index": max((item.z_index for item in clips), default=0), "clips": document_clips})
    existing = dict(settings.get("confirmed_timeline", {}))
    main_asset_id = next(
        (clip["source_asset_id"] for track in tracks if track["type"] == TrackType.MAIN_VIDEO.value for clip in track["clips"]),
        existing.get("source_asset_id"),
    )
    document = {
        **existing, "version": 2, "source_asset_id": main_asset_id, "tracks": tracks,
        "segments": [clip for track in tracks if track["type"] == TrackType.MAIN_VIDEO.value for clip in track["clips"]],
    }
    settings["confirmed_timeline"] = document
    settings["multitrack_timeline"] = document
    timeline.settings_json = settings
