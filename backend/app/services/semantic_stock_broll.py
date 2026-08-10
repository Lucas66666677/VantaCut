"""Timestamped scene-keyword extraction and an attribution-preserving Pexels Video client."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings


class StockBRollError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneKeyword:
    query: str
    label: str
    start_time: float
    end_time: float
    confidence: float


_SCENE_VOCABULARY: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("冰島", "冰川"), "Iceland glacier hiking", "冰島冰川健行"),
    (("冰川",), "glacier hiking", "冰川健行"),
    (("極光",), "northern lights Iceland", "極光"),
    (("公路旅行",), "road trip scenic drive", "公路旅行"),
    (("露營",), "camping mountains", "露營"),
    (("登山", "健行"), "mountain hiking trail", "登山健行"),
    (("雅思", "口說"), "English speaking exam student", "雅思口說考試"),
    (("雅思",), "English language study", "雅思備考"),
    (("咖啡",), "coffee shop barista", "咖啡"),
    (("料理",), "cooking food close up", "料理"),
    (("城市",), "city street cinematic", "城市街景"),
    (("海邊", "海灘"), "ocean beach waves", "海邊"),
)


def _timed_subtitle_items(payload: dict[str, Any]) -> list[tuple[str, float, float]]:
    items = list(dict(payload.get("subtitles", {})).get("items", []))
    if not items:
        items = list(dict(payload.get("transcript", {})).get("segments", []))
    result: list[tuple[str, float, float]] = []
    for item in items:
        text = str(item.get("text", "")).strip()
        start = float(item.get("start_time", item.get("start", 0)))
        end = float(item.get("end_time", item.get("end", start)))
        if text and end > start:
            result.append((text, start, end))
    return result


def extract_scene_keywords(settings_json: dict[str, Any], *, max_clips: int) -> list[SceneKeyword]:
    """Lightweight NLP matcher: phrase-aware, timestamp-preserving, and deliberately explainable."""
    candidates: list[SceneKeyword] = []
    for text, start, end in _timed_subtitle_items(settings_json):
        normalized = re.sub(r"\s+", "", text.lower())
        for required, query, label in _SCENE_VOCABULARY:
            if all(token.lower() in normalized for token in required):
                candidates.append(SceneKeyword(query=query, label=label, start_time=start, end_time=end, confidence=min(.98, .72 + .07 * len(required))))
                break
    # Avoid a repetitive montage: keep the strongest non-overlapping phrase mentions.
    selected: list[SceneKeyword] = []
    seen_queries: set[str] = set()
    for candidate in candidates:
        if candidate.query in seen_queries or any(candidate.start_time < item.end_time + 1.5 and candidate.end_time > item.start_time - 1.5 for item in selected):
            continue
        selected.append(candidate); seen_queries.add(candidate.query)
        if len(selected) >= max_clips:
            break
    return selected


@dataclass(frozen=True)
class PexelsVideo:
    id: int
    download_url: str
    width: int
    height: int
    page_url: str
    creator: str | None
    creator_url: str | None


class PexelsVideoProvider:
    endpoint = "https://api.pexels.com/v1/videos/search"

    def search(self, query: str, *, aspect_ratio: str) -> PexelsVideo:
        if not settings.pexels_api_key:
            raise StockBRollError("PEXELS_API_KEY is required for semantic stock B-Roll")
        orientation = "portrait" if aspect_ratio == "9:16" else "landscape"
        try:
            response = httpx.get(self.endpoint, headers={"Authorization": settings.pexels_api_key}, params={"query": query, "orientation": orientation, "size": "medium", "locale": "zh-TW", "per_page": 8}, timeout=30)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise StockBRollError(f"Pexels search failed: {exc}") from exc
        videos = list(response.json().get("videos", []))
        if not videos:
            raise StockBRollError(f"No Pexels videos found for '{query}'")
        target_portrait = aspect_ratio == "9:16"
        for video in videos:
            files = [file for file in list(video.get("video_files", [])) if file.get("file_type") == "video/mp4" and file.get("link")]
            if not files:
                continue
            files.sort(key=lambda item: (int(item.get("width", 0)) * int(item.get("height", 0))), reverse=True)
            preferred = next((file for file in files if (int(file.get("height", 0)) >= int(file.get("width", 0))) == target_portrait), files[0])
            return PexelsVideo(id=int(video["id"]), download_url=str(preferred["link"]), width=int(preferred.get("width", video.get("width", 0))), height=int(preferred.get("height", video.get("height", 0))), page_url=str(video.get("url", "https://www.pexels.com")), creator=str(video.get("user", {}).get("name") or "") or None, creator_url=str(video.get("user", {}).get("url") or "") or None)
        raise StockBRollError(f"Pexels returned no downloadable MP4 for '{query}'")
