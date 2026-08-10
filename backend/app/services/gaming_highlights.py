"""Signal extraction and weighted decision logic for long-form game-stream highlights."""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


HIGHLIGHT_KEYWORDS = re.compile(
    r"\b(nice|let'?s go|clutch|ace|one tap|got him|dead)\b|好球|漂亮|死了|擊殺|贏了|太扯",
    re.IGNORECASE,
)
KILL_FEED_KEYWORDS = re.compile(r"kill|killed|eliminated|headshot|ace|擊殺|淘汰|爆頭|殺", re.IGNORECASE)


class GamingHighlightError(RuntimeError):
    pass


@dataclass(frozen=True)
class HighlightSignal:
    timestamp: float
    kind: str
    score: float
    detail: str


def detect_audio_spikes(audio_path: str | Path, *, track_name: str, chunk_ms: int = 250) -> list[HighlightSignal]:
    """Find merged loudness bursts relative to the track's own robust noise floor."""
    try:
        from pydub import AudioSegment
    except ImportError as exc:
        raise GamingHighlightError("pydub is required for gaming audio spike detection") from exc
    audio = AudioSegment.from_file(str(audio_path))
    levels: list[tuple[int, float]] = []
    for offset in range(0, len(audio), chunk_ms):
        dbfs = audio[offset : offset + chunk_ms].dBFS
        levels.append((offset, -90.0 if not math.isfinite(dbfs) else dbfs))
    if not levels:
        return []
    values = [level for _, level in levels]
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values) or 1.0
    threshold = max(-35.0, median + max(8.0, mad * 5.0))
    signals: list[HighlightSignal] = []
    active_start: int | None = None
    peak_level = -90.0
    for offset, level in [*levels, (len(audio), -90.0)]:
        if level >= threshold:
            active_start = offset if active_start is None else active_start
            peak_level = max(peak_level, level)
            continue
        if active_start is None:
            continue
        midpoint = (active_start + offset) / 2 / 1000
        intensity = min(2.5, max(0.5, (peak_level - threshold) / 6 + 1.0))
        base_score = 1.35 if track_name == "microphone" else 1.0
        signals.append(HighlightSignal(midpoint, f"{track_name}_spike", base_score * intensity, f"{track_name} peak {peak_level:.1f} dBFS"))
        active_start, peak_level = None, -90.0
    return signals


def detect_kill_feed_events(
    video_path: str | Path,
    *,
    region: tuple[float, float, float, float] = (0.62, 0.0, 1.0, 0.35),
    sample_interval_seconds: float = 1.0,
) -> list[HighlightSignal]:
    """OCR a normalised kill-feed ROI once per second; tune region per game preset."""
    try:
        import cv2
        import pytesseract
    except ImportError as exc:
        raise GamingHighlightError("OpenCV and pytesseract are required for kill-feed OCR") from exc
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise GamingHighlightError(f"Unable to open video for OCR: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    next_sample = 0
    frame_index = 0
    events: list[HighlightSignal] = []
    last_text = ""
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index < next_sample:
                frame_index += 1
                continue
            height, width = frame.shape[:2]
            left, top, right, bottom = region
            x1, y1 = int(width * left), int(height * top)
            x2, y2 = int(width * right), int(height * bottom)
            roi = frame[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]
            if roi.size:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                enlarged = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                thresholded = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
                text = pytesseract.image_to_string(thresholded, config="--psm 6").strip()
                if text and text != last_text and KILL_FEED_KEYWORDS.search(text):
                    events.append(HighlightSignal(frame_index / fps, "kill_feed", 3.0, text[:240]))
                last_text = text
            next_sample += max(1, round(fps * sample_interval_seconds))
            frame_index += 1
    finally:
        capture.release()
    return events


def transcript_reaction_signals(transcript: Any) -> list[HighlightSignal]:
    signals: list[HighlightSignal] = []
    for segment in transcript.segments:
        if HIGHLIGHT_KEYWORDS.search(segment.text):
            signals.append(HighlightSignal((segment.start + segment.end) / 2, "reaction_keyword", 2.2, segment.text))
    return signals


def build_gaming_highlight_timeline(
    signals: list[HighlightSignal],
    *,
    source_asset_id: str,
    max_segments: int = 50,
    pre_roll_seconds: float = 8.0,
    post_roll_seconds: float = 12.0,
) -> dict[str, Any]:
    """Merge correlated signals into reviewable keep segments with transparent reasons."""
    windows = sorted(
        ((max(0.0, signal.timestamp - pre_roll_seconds), signal.timestamp + post_roll_seconds, signal) for signal in signals),
        key=lambda item: item[0],
    )
    merged: list[tuple[float, float, list[HighlightSignal]]] = []
    for start, end, signal in windows:
        if merged and start <= merged[-1][1]:
            previous_start, previous_end, previous_signals = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end), [*previous_signals, signal])
        else:
            merged.append((start, end, [signal]))
    ranked = sorted(merged, key=lambda item: sum(signal.score for signal in item[2]), reverse=True)[:max_segments]
    segments = []
    for start, end, evidence in sorted(ranked, key=lambda item: item[0]):
        total_score = sum(signal.score for signal in evidence)
        confidence = min(99, round(45 + total_score * 8))
        reasons = "; ".join(f"{signal.kind}: {signal.detail}" for signal in evidence[:4])
        segments.append({
            "source_start": round(start, 3),
            "source_end": round(end, 3),
            "action": "keep",
            "confidence_score": confidence,
            "reason": reasons,
            "signals": [signal.__dict__ for signal in evidence],
        })
    return {"version": 1, "kind": "gaming_highlights", "source_asset_id": source_asset_id, "segments": segments}
