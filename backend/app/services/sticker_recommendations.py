"""Lightweight semantic sticker matching over timestamped subtitle sentences."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


LIBRARY_PATH = Path(__file__).resolve().parents[1] / "assets" / "sticker_library.json"


def load_sticker_library() -> list[dict[str, Any]]:
    return list(json.loads(LIBRARY_PATH.read_text(encoding="utf-8")).get("stickers", []))


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _contains(text: str, tag: str) -> bool:
    normalized_tag = _normalise(tag)
    # Chinese tags are not word-delimited; English tags should be phrase bounded.
    if re.search(r"[\u4e00-\u9fff]", normalized_tag):
        return normalized_tag in text
    return bool(re.search(rf"(?<!\w){re.escape(normalized_tag)}(?!\w)", text))


def recommend_stickers(subtitle_cues: list[dict[str, Any]], *, max_items: int = 24) -> list[dict[str, Any]]:
    """Return at most one sticker per cue, preferring explicit noun/phrase matches.

    Cues already carry ASR timing and a kinetic-caption emotion; this keeps the
    recommendation deterministic, explainable and inexpensive.
    """
    recommendations: list[dict[str, Any]] = []
    library = load_sticker_library()
    for cue_index, cue in enumerate(subtitle_cues):
        text = _normalise(str(cue.get("text", "")))
        if not text:
            continue
        emotion = str(cue.get("emotion", "neutral"))
        candidates: list[tuple[int, dict[str, Any], str]] = []
        for sticker in library:
            matches = [tag for tag in sticker.get("tags", []) if _contains(text, str(tag))]
            if matches:
                candidates.append((20 + max(map(len, matches)), sticker, str(matches[0])))
            elif emotion in set(sticker.get("emotions", [])):
                candidates.append((5, sticker, f"emotion:{emotion}"))
        explicit_candidates = [candidate for candidate in candidates if candidate[0] > 5]
        # Emotions decorate a sentence only when no concrete subject/phrase was
        # found. Otherwise every sentence would become visually noisy.
        candidates = explicit_candidates or candidates
        if not candidates:
            continue
        start = float(cue.get("start_time", 0))
        end = float(cue.get("end_time", start + 1))
        seen_stickers: set[str] = set()
        for score, sticker, trigger in sorted(candidates, key=lambda item: item[0], reverse=True):
            if sticker["id"] in seen_stickers:
                continue
            seen_stickers.add(sticker["id"])
            # Alternate corners to avoid a stack of full-screen automatic stickers.
            position = {"x": 0.20 if len(recommendations) % 2 else 0.80, "y": 0.23 if len(recommendations) % 3 else 0.72}
            recommendations.append({
                "id": f"ai-sticker-{cue.get('id', cue_index)}-{sticker['id']}", "sticker_id": sticker["id"],
                "asset_url": sticker["asset_url"], "fallback_emoji": sticker["fallback_emoji"], "label": sticker["label"],
                "source_start": round(start, 3), "source_end": round(max(end + 1.2, start + 1.5), 3),
                "position": position, "scale": 1.0, "rotation": 0.0, "source": "ai", "enabled": True,
                "trigger": {"text": trigger, "emotion": emotion}, "confidence_score": min(98, 55 + score),
            })
            if len(seen_stickers) == 2 or len(recommendations) >= max_items:
                break
        if len(recommendations) >= max_items:
            break
    return recommendations
