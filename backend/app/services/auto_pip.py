"""Audio-driven Auto-PiP planning. Output is declarative Timeline metadata, never media mutation."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


class AutoPipError(RuntimeError):
    pass


def _duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], check=True, capture_output=True, text=True, timeout=60)
    return max(0.0, float(result.stdout.strip() or 0))


def speech_focus_events(video_path: Path, *, timeline_offset: float, minimum_seconds: float) -> list[dict[str, float | str]]:
    """Use non-silent selfie intervals as conservative speech/VAD proxy, merging tiny natural pauses."""
    duration = _duration(video_path)
    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video_path), "-af", "silencedetect=n=-35dB:d=.35", "-f", "null", "-"], capture_output=True, text=True, timeout=20 * 60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise AutoPipError("Selfie speech analysis timed out") from exc
    output = (result.stderr or "") + (result.stdout or "")
    starts = [float(value) for value in re.findall(r"silence_start:\s*([\d.]+)", output)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([\d.]+)", output)]
    silences = list(zip(starts, ends + [duration] * max(0, len(starts) - len(ends))))
    speech: list[list[float]] = []; cursor = 0.0
    for start, end in silences:
        if start > cursor: speech.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < duration: speech.append([cursor, duration])
    merged: list[list[float]] = []
    for start, end in speech:
        if merged and start - merged[-1][1] < .45: merged[-1][1] = end
        else: merged.append([start, end])
    return [{"start_time": round(max(0, start + timeline_offset), 3), "end_time": round(max(0, end + timeline_offset), 3), "mode": "selfie_focus", "transition_seconds": .35} for start, end in merged if end - start >= minimum_seconds]
