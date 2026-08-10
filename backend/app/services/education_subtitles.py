import re
from collections.abc import Iterable
from typing import Any

from app.ai.education_prompts import (
    EDUCATION_KEYWORD_SYSTEM_PROMPT,
    EDUCATION_KEYWORD_USER_PROMPT,
    education_keyword_response_schema,
)
from app.ai.providers.factory import get_text_provider
from app.ai.providers.schemas import DeliveryHint, Transcript, WordTimestamp
from app.schemas.education import EducationKeyword, EducationKeywordResult


FAST_SPEECH_WPM = 180.0
LONG_PAUSE_SECONDS = 1.2
REMOVE_PAUSE_SECONDS = 2.5


class EducationSubtitleError(RuntimeError):
    pass


def _spoken_units(words: Iterable[WordTimestamp]) -> int:
    units = 0
    for word in words:
        token = word.word.strip()
        units += len(re.findall(r"[\u4e00-\u9fff]", token))
        units += len(re.findall(r"[A-Za-z0-9']+", token))
    return units


def analyze_delivery(transcript: Transcript, silences: list[dict[str, Any]]) -> list[DeliveryHint]:
    hints: list[DeliveryHint] = []
    for segment in transcript.segments:
        duration = segment.end - segment.start
        if duration < 1.0:
            continue
        wpm = _spoken_units(segment.words) / duration * 60
        if wpm > FAST_SPEECH_WPM:
            hints.append(DeliveryHint(
                start=segment.start,
                end=segment.end,
                kind="fast_speech",
                message=f"語速約 {wpm:.0f} WPM，觀眾可能難以吸收。",
                suggested_action="slow_down_or_add_card",
                confidence=min(1.0, 0.75 + (wpm - FAST_SPEECH_WPM) / 300),
                words_per_minute=round(wpm, 1),
            ))
    for silence in silences:
        start = float(silence["start"])
        end = float(silence["end"])
        duration = end - start
        if duration >= LONG_PAUSE_SECONDS:
            should_remove = duration >= REMOVE_PAUSE_SECONDS
            hints.append(DeliveryHint(
                start=start,
                end=end,
                kind="long_pause",
                message=f"停頓 {duration:.1f} 秒。",
                suggested_action="remove" if should_remove else "add_card_or_slow_down",
                confidence=0.95,
            ))
    return sorted(hints, key=lambda hint: (hint.start, hint.end))


def extract_education_keywords(transcript: Transcript) -> list[EducationKeyword]:
    provider = get_text_provider()
    raw_result = provider.extract_education_keywords(
        transcript.text,
        system_prompt=EDUCATION_KEYWORD_SYSTEM_PROMPT,
        user_prompt=f"{EDUCATION_KEYWORD_USER_PROMPT}\n\nTRANSCRIPT:\n{transcript.text}",
        response_schema=education_keyword_response_schema(),
    )
    try:
        return EducationKeywordResult.model_validate(raw_result).keywords
    except Exception as exc:
        raise EducationSubtitleError("Text provider returned invalid educational keyword JSON") from exc


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", text.casefold()).strip()


def _find_term_occurrences(term: str, words: list[WordTimestamp]) -> list[tuple[float, float]]:
    target_tokens = _normalize(term).split()
    normalized_words = [_normalize(word.word) for word in words]
    if not target_tokens:
        return []
    occurrences: list[tuple[float, float]] = []
    for index in range(len(words) - len(target_tokens) + 1):
        current = normalized_words[index:index + len(target_tokens)]
        if current == target_tokens:
            occurrences.append((words[index].start, words[index + len(target_tokens) - 1].end))
    return occurrences


def build_text_overlays(keywords: list[EducationKeyword], transcript: Transcript) -> list[dict[str, Any]]:
    words = sorted(
        [word for segment in transcript.segments for word in segment.words],
        key=lambda word: (word.start, word.end),
    )
    overlays: list[dict[str, Any]] = []
    for keyword in keywords:
        for start, end in _find_term_occurrences(keyword.term, words):
            overlays.append({
                "id": f"keyword-{len(overlays) + 1}",
                "track": "education_text",
                "type": "tooltip_graphic",
                "source_start": round(start, 3),
                "source_end": round(max(end + 2.5, start + 1.5), 3),
                "text": keyword.term,
                "tooltip": keyword.explanation,
                "category": keyword.category,
                "importance": keyword.importance,
                "style": {"preset": "education_keyword_card", "position": "bottom"},
            })
    return overlays

