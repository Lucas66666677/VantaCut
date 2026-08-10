"""Validated built-in template parsing plus deterministic beat-aligned timeline assembly."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class OneClickTemplateError(ValueError):
    pass


@dataclass(frozen=True)
class TemplateSlot:
    id: str
    beats: int
    transition_after: str | None = None


@dataclass(frozen=True)
class OneClickTemplate:
    id: str
    name: str
    aspect_ratio: str
    target_bpm: float
    bgm: dict[str, Any]
    slots: tuple[TemplateSlot, ...]


def _manifest_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "one_click_templates.json"


def _parse_template(raw: dict[str, Any]) -> OneClickTemplate:
    try:
        bgm, slots = dict(raw["bgm"]), list(raw["slots"])
        template = OneClickTemplate(
            id=str(raw["id"]), name=str(raw["name"]), aspect_ratio=str(raw.get("aspect_ratio", "9:16")),
            target_bpm=float(bgm["target_bpm"]), bgm=bgm,
            slots=tuple(TemplateSlot(str(item["id"]), int(item["beats"]), item.get("transition_after")) for item in slots),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OneClickTemplateError("Invalid one-click template manifest") from exc
    if template.aspect_ratio not in {"16:9", "9:16"} or not 40 <= template.target_bpm <= 240 or not template.slots:
        raise OneClickTemplateError("Template BPM, aspect ratio, or slots are invalid")
    if any(not slot.id or not 1 <= slot.beats <= 32 for slot in template.slots):
        raise OneClickTemplateError("Template slot beats must be between 1 and 32")
    return template


def list_templates() -> list[OneClickTemplate]:
    try:
        manifest = json.loads(_manifest_path().read_text(encoding="utf-8"))
        return [_parse_template(dict(item)) for item in manifest["templates"]]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise OneClickTemplateError("Unable to load one-click template manifest") from exc


def get_template(template_id: str) -> OneClickTemplate:
    for template in list_templates():
        if template.id == template_id:
            return template
    raise OneClickTemplateError(f"Unknown template: {template_id}")


def template_summary(template: OneClickTemplate) -> dict[str, Any]:
    return {"id": template.id, "name": template.name, "aspect_ratio": template.aspect_ratio, "bgm": template.bgm, "slot_count": len(template.slots), "total_beats": sum(slot.beats for slot in template.slots)}


def beat_times(*, template: OneClickTemplate, detected_beats: list[float] | None = None) -> list[float]:
    required = sum(slot.beats for slot in template.slots) + 1
    valid = sorted(float(item) for item in detected_beats or [] if float(item) >= 0)
    if len(valid) >= required:
        return valid[:required]
    interval = 60.0 / template.target_bpm
    return [round(index * interval, 6) for index in range(required)]


def build_template_timeline(
    *, template: OneClickTemplate, ranked_candidates: list[dict[str, Any]], detected_beats: list[float] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not ranked_candidates:
        raise OneClickTemplateError("No usable media candidates")
    beats = beat_times(template=template, detected_beats=detected_beats)
    clips: list[dict[str, Any]] = []; transitions: list[dict[str, Any]] = []; cursor = 0
    for index, slot in enumerate(template.slots):
        candidate = ranked_candidates[index % len(ranked_candidates)]
        duration = max(.25, beats[cursor + slot.beats] - beats[cursor])
        source_duration = float(candidate["source_end"]) - float(candidate["source_start"])
        source_start = float(candidate["source_start"])
        # Candidate windows are always at least a slot long; the clamp protects hand-authored manifests.
        source_end = source_start + min(duration, source_duration)
        clip_id = f"one-click-{index + 1}-{candidate['asset_id']}"
        clips.append({
            "id": clip_id, "source_asset_id": str(candidate["asset_id"]), "source_start": round(source_start, 3),
            "source_end": round(source_end, 3), "timeline_start": round(beats[cursor], 3), "action": "keep",
            "confidence_score": round(float(candidate["score"]) * 100, 1), "reason": "AI selected for clarity, face presence, and motion.",
        })
        if slot.transition_after and index < len(template.slots) - 1:
            transitions.append({"id": f"template-transition-{index + 1}", "from_clip_id": clip_id, "to_clip_id": f"one-click-{index + 2}-{ranked_candidates[(index + 1) % len(ranked_candidates)]['asset_id']}", "kind": slot.transition_after, "duration_seconds": min(.35, duration / 3), "fallback_xfade": "fade"})
        cursor += slot.beats
    document = {
        "schema": "com.aivideo.one-click-timeline.v1", "source_asset_id": clips[0]["source_asset_id"],
        "tracks": [{"id": "main-video", "type": "main_video", "z_index": 0, "clips": clips}],
        "template": {"id": template.id, "name": template.name, "aspect_ratio": template.aspect_ratio, "bgm": template.bgm, "beat_times": beats},
        "transition_graph": {"version": 1, "transitions": transitions},
    }
    return document, transitions
