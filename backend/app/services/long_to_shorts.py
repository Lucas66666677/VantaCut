"""Explainable transcript + visual-motion selection for long-form short-video candidates."""
from __future__ import annotations

import re
from typing import Any


FILLERS = frozenset({"呃", "嗯", "啊", "然後", "那個", "uh", "um"})


def _tokens(text: str) -> list[str]:
    return [item for item in re.split(r"[\s，。！？,.!?;；:：]+", text.lower()) if item and item not in FILLERS]


def select_short_candidates(*, transcript: dict[str, Any], duration_seconds: float, visual_events: list[dict[str, Any]] | None = None, count: int = 3, min_duration: float = 45, max_duration: float = 60) -> list[dict[str, Any]]:
    """Pick non-overlapping self-contained 45–60s windows using information density + visual motion."""
    segments = [dict(item) for item in transcript.get("segments", []) if isinstance(item, dict) and float(item.get("end", 0)) > float(item.get("start", 0))]
    candidates: list[dict[str, Any]] = []
    visual_events = visual_events or []
    for first in range(len(segments)):
        start = float(segments[first]["start"]); words: list[str] = []; last = first
        for index in range(first, len(segments)):
            end = float(segments[index]["end"]); window = end - start
            if window > max_duration:
                break
            words.extend(_tokens(str(segments[index].get("text", "")))); last = index
            if window < min_duration:
                continue
            visual = [float(item.get("score", 0)) for item in visual_events if start <= float(item.get("time", -1)) <= end]
            density = len(words) / max(window, 1)
            closure_bonus = .22 if re.search(r"[。！？.!?]$", str(segments[last].get("text", "")).strip()) else 0
            score = density * .72 + (sum(visual) / max(1, len(visual))) * .28 + closure_bonus
            candidates.append({"source_start": round(start, 3), "source_end": round(end, 3), "duration": round(window, 3), "info_density": round(density, 3), "visual_score": round(sum(visual) / max(1, len(visual)), 3), "score": round(score, 4), "text": " ".join(str(item.get("text", "")) for item in segments[first:last + 1]).strip()})
    selected: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
        if any(not (candidate["source_end"] <= old["source_start"] + 8 or candidate["source_start"] >= old["source_end"] - 8) for old in selected):
            continue
        selected.append(candidate)
        if len(selected) == count:
            break
    # Deterministic fallback remains useful when ASR has sparse segments.
    if len(selected) < count:
        span = min(max_duration, max(min_duration, duration_seconds / max(1, count)))
        for index in range(count * 2):
            start = min(max(0.0, index * max(1.0, (duration_seconds - span) / max(1, count - 1))), max(0.0, duration_seconds - span)); end = min(duration_seconds, start + span)
            candidate = {"source_start": round(start, 3), "source_end": round(end, 3), "duration": round(end - start, 3), "info_density": 0.0, "visual_score": 0.0, "score": 0.0, "text": ""}
            if end > start and not any(abs(candidate["source_start"] - old["source_start"]) < 8 for old in selected): selected.append(candidate)
            if len(selected) == count: break
    return sorted(selected[:count], key=lambda item: item["source_start"])


def fallback_hook_title(text: str, index: int) -> str:
    tokens = _tokens(text)
    subject = " ".join(tokens[:7]).strip() or "這個重點"
    return f"原來{subject}要這樣做…"[:38] if index == 0 else f"這段{subject}，真的別錯過"[:38]
