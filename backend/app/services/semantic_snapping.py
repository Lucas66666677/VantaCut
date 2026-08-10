"""Build output-time snapping guides from persisted AI-analysis results.

The editor always deals in *output* time.  This module therefore maps source-time
analysis (ASR pauses, gameplay spikes and optical-flow peaks) through the kept main
track rather than leaking raw source offsets to the browser.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.entities import AIAnalysis, AnalysisType, Timeline


@dataclass(frozen=True)
class OutputSegment:
    asset_id: str | None
    source_start: float
    source_end: float
    output_start: float


def _as_float(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _tracks(timeline: Timeline) -> list[dict[str, Any]]:
    settings = dict(timeline.settings_json or {})
    document = settings.get("multitrack_timeline") or settings.get("confirmed_timeline") or {}
    tracks = [dict(track) for track in document.get("tracks", []) if isinstance(track, dict)]
    if tracks:
        return tracks
    # Fresh timelines may have normalized Clip rows before the editor has persisted a
    # JSON document.  Keep snapping useful during that first edit session too.
    clips = [
        {
            "source_asset_id": str(clip.source_asset_id),
            "source_start": float(clip.source_start),
            "source_end": float(clip.source_end),
            "action": "keep" if clip.enabled else "remove",
            "track": clip.track.value if hasattr(clip.track, "value") else str(clip.track),
        }
        for clip in timeline.clips
    ]
    return [{"type": "main_video", "clips": [clip for clip in clips if clip["track"] == "main_video"]}]


def output_segments(timeline: Timeline) -> list[OutputSegment]:
    """Return kept main-track source ranges and their ripple-deleted output starts."""
    main = next((track for track in _tracks(timeline) if track.get("type") == "main_video"), {})
    clips = [dict(clip) for clip in main.get("clips", []) if isinstance(clip, dict)]
    clips.sort(key=lambda clip: float(clip.get("source_start", 0)))
    cursor = 0.0
    result: list[OutputSegment] = []
    for clip in clips:
        if clip.get("action", "keep") != "keep" or clip.get("review_status") == "cut":
            continue
        start, end = _as_float(clip.get("source_start")), _as_float(clip.get("source_end"))
        if start is None or end is None or end <= start:
            continue
        result.append(OutputSegment(str(clip["source_asset_id"]) if clip.get("source_asset_id") else None, start, end, cursor))
        cursor += end - start
    return result


def source_to_output(segments: Iterable[OutputSegment], asset_id: str, source_time: float) -> float | None:
    for segment in segments:
        if segment.asset_id == asset_id and segment.source_start - .001 <= source_time <= segment.source_end + .001:
            return round(segment.output_start + max(0.0, min(source_time, segment.source_end) - segment.source_start), 3)
    return None


def _latest_completed(db: Session, asset_ids: set[str]) -> list[AIAnalysis]:
    if not asset_ids:
        return []
    rows = db.query(AIAnalysis).filter(AIAnalysis.status == "completed").order_by(AIAnalysis.created_at.desc()).all()
    seen: set[tuple[str, str]] = set()
    latest: list[AIAnalysis] = []
    for row in rows:
        key = (str(row.media_asset_id), str(row.analysis_type.value if hasattr(row.analysis_type, "value") else row.analysis_type))
        if key[0] not in asset_ids or key in seen:
            continue
        seen.add(key)
        latest.append(row)
    return latest


def _point(*, point_id: str, time: float, kind: str, strength: float, label: str, asset_id: str | None = None) -> dict[str, Any]:
    return {"id": point_id, "time_seconds": round(max(0.0, time), 3), "type": kind, "strength": max(0.0, min(1.0, strength)), "label": label, "source_asset_id": asset_id}


def _deduplicate(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the strongest marker of the same type within a two-frame tolerance."""
    ordered = sorted(points, key=lambda point: (point["type"], point["time_seconds"], -point["strength"]))
    result: list[dict[str, Any]] = []
    for point in ordered:
        duplicate = next((item for item in reversed(result) if item["type"] == point["type"] and abs(item["time_seconds"] - point["time_seconds"]) < .067), None)
        if duplicate is None:
            result.append(point)
        elif point["strength"] > duplicate["strength"]:
            result[result.index(duplicate)] = point
    return sorted(result, key=lambda point: point["time_seconds"])


def build_semantic_snap_points(db: Session, timeline: Timeline) -> list[dict[str, Any]]:
    settings = dict(timeline.settings_json or {})
    points: list[dict[str, Any]] = []

    # Music markers are already in output time for a beat montage; standard beat analysis
    # starts at the BGM origin, which maps to the output timeline at zero.
    beat_sync = dict(settings.get("beat_sync") or {})
    montage = dict(settings.get("beat_sync_montage") or {})
    music = dict(beat_sync.get("music") or {})
    downbeats = montage.get("downbeats") or music.get("downbeats") or []
    for index, value in enumerate(downbeats):
        time = _as_float(value)
        if time is not None:
            points.append(_point(point_id=f"downbeat-{index}", time=time, kind="downbeat", strength=.92, label="BGM 重拍"))

    segments = output_segments(timeline)
    asset_ids = {segment.asset_id for segment in segments if segment.asset_id}
    for analysis in _latest_completed(db, {value for value in asset_ids if value}):
        asset_id = str(analysis.media_asset_id)
        payload = dict(analysis.result_json or {})
        analysis_type = analysis.analysis_type.value if hasattr(analysis.analysis_type, "value") else str(analysis.analysis_type)

        # Rough-cut silences and ASR gaps represent natural sentence boundaries.
        if analysis_type in {AnalysisType.ROUGH_CUT.value, AnalysisType.TRANSCRIPTION.value}:
            pauses = list(payload.get("silences") or [])
            transcript = dict(payload.get("transcript") or {})
            transcript_segments = [dict(item) for item in transcript.get("segments", []) if isinstance(item, dict)]
            transcript_segments.sort(key=lambda item: float(item.get("start", 0)))
            for left, right in zip(transcript_segments, transcript_segments[1:]):
                end, start = _as_float(left.get("end")), _as_float(right.get("start"))
                if end is not None and start is not None and start - end >= .28:
                    pauses.append({"start": end, "end": start, "source": "asr_gap"})
            for index, pause in enumerate(pauses):
                start, end = _as_float(pause.get("start")), _as_float(pause.get("end"))
                if start is None or end is None or end < start:
                    continue
                output_time = source_to_output(segments, asset_id, (start + end) / 2)
                if output_time is not None:
                    duration = end - start
                    points.append(_point(point_id=f"pause-{asset_id}-{index}", time=output_time, kind="speech_pause", strength=min(.9, .48 + duration / 2), label="語句停頓", asset_id=asset_id))

        # Gaming signals and visual-momentum peaks are treated as high-tension frames.
        if analysis_type == AnalysisType.GAMING_HIGHLIGHTS.value:
            for segment_index, segment in enumerate(payload.get("segments") or []):
                if not isinstance(segment, dict):
                    continue
                for signal_index, signal in enumerate(segment.get("signals") or []):
                    if not isinstance(signal, dict):
                        continue
                    source_time, score = _as_float(signal.get("timestamp")), _as_float(signal.get("score"))
                    if source_time is None:
                        continue
                    output_time = source_to_output(segments, asset_id, source_time)
                    if output_time is not None:
                        points.append(_point(point_id=f"game-{asset_id}-{segment_index}-{signal_index}", time=output_time, kind="action_peak", strength=min(1, .45 + (score or 0) / 5), label=str(signal.get("kind") or "遊戲高光"), asset_id=asset_id))

    for index, event in enumerate(beat_sync.get("visual_momentum") or []):
        if not isinstance(event, dict):
            continue
        source_time = _as_float(event.get("time"))
        source_asset_id = str(beat_sync.get("source_asset_id") or "")
        if source_time is None or not source_asset_id:
            continue
        output_time = source_to_output(segments, source_asset_id, source_time)
        if output_time is not None:
            points.append(_point(point_id=f"motion-{index}", time=output_time, kind="action_peak", strength=min(1, .45 + float(event.get("score", 0)) / 8), label="畫面動作張力峰值", asset_id=source_asset_id))
    return _deduplicate(points)
