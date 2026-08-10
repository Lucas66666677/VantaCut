"""Deterministic visual-event to licensed SFX-track mapping and FFmpeg mixing."""
from __future__ import annotations

from pathlib import Path
from typing import Any


SFX_EVENT_MAP = {
    "spring": "pop", "pop": "pop", "shake": "whoosh", "explode": "impact",
    "zoom_blur": "whoosh", "glitch": "whoosh", "rgb_split": "whoosh",
    "depth_person_through": "whoosh", "depth_background_peel": "whoosh", "morph_cut": "whoosh",
}


def _main_clip_end_times(document: dict[str, Any]) -> dict[str, float]:
    cursor = 0.0; ends: dict[str, float] = {}
    for track in document.get("tracks", []):
        if track.get("type") != "main_video":
            continue
        for clip in track.get("clips", []):
            if clip.get("action", "keep") != "keep":
                continue
            cursor += max(0.0, float(clip.get("source_end", 0)) - float(clip.get("source_start", 0)))
            if clip.get("id") is not None:
                ends[str(clip["id"])] = cursor
    return ends


def derive_auto_sfx_events(
    *, confirmed_timeline: dict[str, Any], subtitles: dict[str, Any], transition_graph: dict[str, Any], asset_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Map kinetic word/transition events to user-owned SFX assets with short cooldowns."""
    events: list[dict[str, Any]] = []
    for cue in subtitles.get("items", []):
        for word in cue.get("words", []):
            kind = SFX_EVENT_MAP.get(str(word.get("animation_preset", "none")))
            if kind and asset_map.get(kind):
                events.append({"kind": kind, "timeline_start": float(word.get("start", cue.get("start_time", 0))), "duration": .45, "gain_db": -7.0, "source_asset_id": asset_map[kind], "reason": f"kinetic_caption:{word.get('animation_preset')}"})
    ends = _main_clip_end_times(confirmed_timeline)
    for transition in transition_graph.get("transitions", []):
        kind = SFX_EVENT_MAP.get(str(transition.get("kind", "")))
        source_asset_id = asset_map.get(kind or "")
        if not kind or not source_asset_id:
            continue
        timestamp = float(transition.get("output_time", ends.get(str(transition.get("from_clip_id", "")), 0.0)))
        events.append({"kind": kind, "timeline_start": max(0.0, timestamp - .08), "duration": .65, "gain_db": -5.0, "source_asset_id": source_asset_id, "reason": f"transition:{transition.get('kind')}"})
    deduplicated: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda item: float(item["timeline_start"])):
        if any(event["kind"] == old["kind"] and abs(float(event["timeline_start"]) - float(old["timeline_start"])) < .12 for old in deduplicated):
            continue
        deduplicated.append({**event, "id": f"auto-sfx-{len(deduplicated) + 1}"})
    return deduplicated


def build_auto_sfx_mix_command(
    *, video_path: str, output_path: str, sfx_events: list[dict[str, Any]], bgm_path: str | None = None,
    bgm_volume: float = .16, ducking: dict[str, float] | None = None,
    tape_stop_events: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Mix speaker/dialogue, ducked BGM, and timestamp-aligned SFX into one programme track."""
    ducking = ducking or {}; tape_stop_events = tape_stop_events or []
    command = ["ffmpeg", "-y", "-i", video_path]; filters: list[str] = []
    input_index = 1; bgm_index: int | None = None
    if bgm_path:
        bgm_index = input_index; input_index += 1; command.extend(["-stream_loop", "-1", "-i", bgm_path])
    for event in sfx_events:
        command.extend(["-i", str(event["local_path"])])
    mix_labels = ["dialogue"]
    if bgm_index is not None:
        threshold, ratio = float(ducking.get("threshold", .035)), float(ducking.get("ratio", 8.0))
        attack, release = int(ducking.get("attack_ms", 20)), int(ducking.get("release_ms", 280))
        filters.append("[0:a]asplit=2[dialogue][speech_key]")
        bgm_label = "bgm"
        filters.append(f"[{bgm_index}:a]volume={max(0, min(.9, bgm_volume)):.4f},asetpts=PTS-STARTPTS[{bgm_label}]")
        # A short BGM-only mute plus a decelerating synthetic chirp sells the
        # tape-stop joke without bundling a third-party comedy sound effect.
        for index, event in enumerate(tape_stop_events):
            start = max(0.0, float(event.get("timeline_start", 0.0)))
            duration = min(.5, max(.08, float(event.get("duration", .22))))
            next_label = f"tape_bgm_{index}"
            filters.append(f"[{bgm_label}]volume=volume='if(between(t\\,{start:.4f}\\,{start + duration:.4f})\\,0\\,1)':eval=frame[{next_label}]")
            delay = round(start * 1000)
            filters.append(f"aevalsrc=exprs='0.13*sin(2*PI*(330*(1-t/{duration:.4f})*(1-t/{duration:.4f})+35)*t)':s=48000:d={duration:.4f},adelay={delay}:all=1[tape_stop_{index}]")
            mix_labels.append(f"tape_stop_{index}")
            bgm_label = next_label
        if bool(ducking.get("enabled", True)):
            filters.append(f"[{bgm_label}][speech_key]sidechaincompress=threshold={threshold:.4f}:ratio={ratio:.3f}:attack={attack}:release={release}[ducked_bgm]")
        else:
            filters.append(f"[{bgm_label}]anull[ducked_bgm]")
        mix_labels.append("ducked_bgm")
    else:
        filters.append("[0:a]anull[dialogue]")
    for offset, event in enumerate(sfx_events):
        label = f"sfx{offset}"; delay = max(0, round(float(event["timeline_start"]) * 1000)); duration = max(.05, float(event.get("duration", .5)))
        filters.append(f"[{input_index + offset}:a]atrim=duration={duration:.4f},volume={float(event.get('gain_db', -7)):.2f}dB,adelay={delay}:all=1[{label}]")
        mix_labels.append(label)
    filters.append(f"{''.join(f'[{label}]' for label in mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0[mix]")
    return command + ["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac", "-shortest", output_path]
