"""Timestamp-safe LLM language review and educational overlay projection."""
from __future__ import annotations

import re
from typing import Any

from app.ai.language_review_prompts import LANGUAGE_REVIEW_SYSTEM_PROMPT, build_language_review_prompt, language_review_response_schema
from app.ai.providers.factory import get_text_provider
from app.ai.providers.schemas import Transcript, WordTimestamp
from app.services.audio_description import main_keep_segments


class LanguageReviewError(RuntimeError):
    pass


def transcript_words(transcript: Transcript) -> list[WordTimestamp]:
    return sorted((word for segment in transcript.segments for word in segment.words), key=lambda item: (item.start, item.end))


def pronunciation_proxy(transcript: Transcript, silences: list[dict[str, Any]]) -> dict[str, Any]:
    words = transcript_words(transcript)
    confidences = [word.confidence for word in words if word.confidence is not None]
    spoken_seconds = sum(max(0, segment.end - segment.start) for segment in transcript.segments)
    token_count = len(words)
    wpm = token_count / spoken_seconds * 60 if spoken_seconds else 0
    return {"asr_word_confidence_mean": round(sum(confidences) / len(confidences), 3) if confidences else None, "word_count": token_count, "estimated_wpm": round(wpm, 1), "pause_count": len(silences), "pronunciation_note": "ASR/prosody proxy only; not phoneme-level assessment"}


def _project_source_time(source_time: float, segments: list[dict[str, float]]) -> float | None:
    for segment in segments:
        if segment["source_start"] <= source_time <= segment["source_end"]:
            return segment["output_start"] + source_time - segment["source_start"]
    return None


def _selected_text(words: list[WordTimestamp], start: int, end: int) -> str:
    return " ".join(item.word.strip() for item in words[start:end + 1]).strip()


def run_language_review(*, transcript: Transcript, silences: list[dict[str, Any]], confirmed_timeline: dict[str, Any], target: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    words = transcript_words(transcript)
    if not words:
        raise LanguageReviewError("Word-level ASR timestamps are required for language review")
    word_payload = [{"index": index, "word": word.word, "start": round(word.start, 3), "end": round(word.end, 3)} for index, word in enumerate(words)]
    proxy = pronunciation_proxy(transcript, silences)
    provider = get_text_provider()
    raw = provider.generate_structured_json(
        system_prompt=LANGUAGE_REVIEW_SYSTEM_PROMPT,
        user_prompt=build_language_review_prompt(words=word_payload, fluency_features=proxy, target=target),
        response_schema=language_review_response_schema(),
    )
    if not isinstance(raw, dict) or not isinstance(raw.get("issues"), list) or not isinstance(raw.get("scores"), dict):
        raise LanguageReviewError("Language model returned an invalid review schema")
    for dimension in ("fluency_coherence", "lexical_resource", "grammatical_range_accuracy", "pronunciation"):
        score = raw["scores"].get(dimension)
        if not isinstance(score, dict) or not 0 <= float(score.get("band_estimate", -1)) <= 9 or not 0 <= float(score.get("confidence", -1)) <= 1:
            raise LanguageReviewError(f"Language model returned an invalid {dimension} score")
    segments = main_keep_segments(confirmed_timeline)
    issues: list[dict[str, Any]] = []
    overlays: list[dict[str, Any]] = []
    for index, item in enumerate(raw["issues"]):
        if not isinstance(item, dict):
            continue
        start_index, end_index = int(item.get("word_start_index", -1)), int(item.get("word_end_index", -1))
        if start_index < 0 or end_index < start_index or end_index >= len(words):
            continue
        original = _selected_text(words, start_index, end_index)
        # The model cannot invent a correction target outside of the immutable ASR token span.
        if re.sub(r"\s+", " ", str(item.get("original_text", "")).strip()) != original:
            continue
        source_start, source_end = words[start_index].start, words[end_index].end
        output_start, output_end = _project_source_time(source_start, segments), _project_source_time(source_end, segments)
        if output_start is None or output_end is None:
            continue
        issue = {"id": str(item.get("id") or f"language-{index + 1}"), "source_start": round(source_start, 3), "source_end": round(source_end, 3), "output_start": round(output_start, 3), "output_end": round(max(output_end, output_start + .1), 3), "category": str(item.get("category", "word_choice")), "original_text": original, "correction": str(item.get("correction", "")).strip(), "explanation": str(item.get("explanation", "")).strip(), "confidence": float(item.get("confidence", 0)), "synonyms": list(item.get("synonyms", []))[:3]}
        if not issue["correction"] or not .78 <= issue["confidence"] <= 1:
            continue
        issues.append(issue)
        overlays.append({"id": f"correction-{issue['id']}", "type": "grammar_correction", "source_start": issue["source_start"], "source_end": issue["source_end"], "output_start": issue["output_start"], "output_end": round(issue["output_end"] + 2.2, 3), "original_text": original, "correction": issue["correction"], "explanation": issue["explanation"], "category": issue["category"], "style": {"preset": "red_strike_green_fix", "position": "top"}})
        if issue["synonyms"]:
            overlays.append({"id": f"synonyms-{issue['id']}", "type": "synonym_card", "source_start": issue["source_start"], "source_end": issue["source_end"], "output_start": issue["output_start"], "output_end": round(issue["output_end"] + 3.4, 3), "term": original, "synonyms": issue["synonyms"], "style": {"preset": "advanced_synonym_card", "position": "bottom_right"}})
    return {"target": target, "scores": raw["scores"], "overall_feedback": str(raw.get("overall_feedback", "")), "disclaimer": str(raw.get("disclaimer", "AI 教學估分，非官方 IELTS 成績。")), "pronunciation_proxy": proxy, "issues": issues}, overlays
