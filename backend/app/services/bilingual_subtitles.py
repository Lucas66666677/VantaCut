"""Context-aware subtitle translation and dual-language export/render helpers."""
from __future__ import annotations

import json
from typing import Any

from app.ai.providers.base import TextAnalysisProvider
from app.ai.providers.factory import get_text_provider
from app.schemas.subtitle import SubtitleCue
from app.services.subtitles import cues_to_ass, cues_to_srt, cues_to_vtt


class BilingualSubtitleError(RuntimeError):
    pass


def translation_system_prompt() -> str:
    return """You are a professional audiovisual subtitle localizer. Translate natural spoken dialogue, not word-for-word prose. Preserve intent, register, jokes, pronouns, and terminology across adjacent cues. Use the supplied document context and glossary. Do not add explanations, timestamps, speaker labels, markdown, or content that was not spoken. Keep each target subtitle concise enough to read within the original cue duration. Return only JSON that exactly matches the requested schema."""


def _response_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": ["translations"], "properties": {"translations": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "text"], "properties": {"id": {"type": "string"}, "text": {"type": "string", "minLength": 1}}}}}}


def _translation_prompt(cues: list[SubtitleCue], *, source_language: str | None, target_language: str, glossary: list[dict[str, Any]], context_cues: list[SubtitleCue] | None = None) -> str:
    document_context = " ".join(cue.text for cue in (context_cues or cues))
    payload = [{"id": cue.id, "source_text": cue.text, "start_time": cue.start_time, "end_time": cue.end_time, "previous_text": cues[index - 1].text if index else None, "next_text": cues[index + 1].text if index + 1 < len(cues) else None} for index, cue in enumerate(cues)]
    return "BILINGUAL_SUBTITLE_TRANSLATION\n" + f"SOURCE_LANGUAGE: {source_language or 'auto-detect'}\nTARGET_LANGUAGE: {target_language}\nDOCUMENT_CONTEXT: {document_context}\nAPPROVED_GLOSSARY: {json.dumps(glossary, ensure_ascii=False)}\nCUES_TO_TRANSLATE: {json.dumps(payload, ensure_ascii=False)}"


def translate_cues_contextually(cues: list[SubtitleCue], *, source_language: str | None, target_language: str, glossary: list[dict[str, Any]] | None = None, provider: TextAnalysisProvider | None = None) -> list[dict[str, Any]]:
    """Translate in bounded batches while including adjacent and document-level context."""
    if not cues:
        raise BilingualSubtitleError("No source subtitle cues are available")
    provider = provider or get_text_provider()
    translated: list[dict[str, Any]] = []
    glossary = glossary or []
    for offset in range(0, len(cues), 48):
        batch = cues[offset:offset + 48]
        context = cues[max(0, offset - 4):min(len(cues), offset + len(batch) + 4)]
        raw = provider.generate_structured_json(system_prompt=translation_system_prompt(), user_prompt=_translation_prompt(batch, source_language=source_language, target_language=target_language, glossary=glossary, context_cues=context), response_schema=_response_schema())
        received = {str(item.get("id")): str(item.get("text", "")).strip() for item in raw.get("translations", []) if isinstance(item, dict)}
        expected = {cue.id for cue in batch}
        if set(received) != expected or any(not text for text in received.values()):
            raise BilingualSubtitleError("Translation provider returned incomplete or invalid cue IDs")
        translated.extend([{"id": cue.id, "start_time": cue.start_time, "end_time": cue.end_time, "source_text": cue.text, "target_text": received[cue.id]} for cue in batch])
    return translated


def bilingual_to_srt(items: list[dict[str, Any]]) -> str:
    return cues_to_srt([SubtitleCue(id=str(item["id"]), start_time=float(item["start_time"]), end_time=float(item["end_time"]), text=f"{item['source_text']}\n{item['target_text']}") for item in items])


def bilingual_to_vtt(items: list[dict[str, Any]]) -> str:
    return cues_to_vtt([SubtitleCue(id=str(item["id"]), start_time=float(item["start_time"]), end_time=float(item["end_time"]), text=f"{item['source_text']}\n{item['target_text']}") for item in items])


def _ass_time(seconds: float) -> str:
    total = max(0, round(seconds * 100)); hours, remainder = divmod(total, 360000); minutes, remainder = divmod(remainder, 6000); whole, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{whole:02}.{centiseconds:02}"


def bilingual_to_ass(cues: list[SubtitleCue], items: list[dict[str, Any]], *, preset: str, aspect_ratio: str) -> str:
    """Keep the source line kinetic; render its translation as a smaller stable second line."""
    width, height = (1080, 1920) if aspect_ratio == "9:16" else (1920, 1080)
    primary = cues_to_ass(cues, preset=preset, aspect_ratio=aspect_ratio, primary_y_ratio=.72)
    marker = "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    header, source_events = primary.split(marker, 1)
    bilingual_header = header + "Style: Translation,Noto Sans,34,&H00FFFFFF,&H000000FF,&H00101010,&H60000000,0,0,0,0,100,100,0,0,1,2,1,2,60,60,96,1\n\n" + marker
    translations = {str(item["id"]): str(item["target_text"]) for item in items}
    lines = []
    for cue in cues:
        text = translations.get(cue.id)
        if not text:
            raise BilingualSubtitleError(f"Missing translation for {cue.id}")
        escaped = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
        lines.append(f"Dialogue: 2,{_ass_time(cue.start_time)},{_ass_time(max(cue.end_time, cue.start_time + .05))},Translation,,0,0,0,,{{\\an5\\pos({width / 2:.0f},{height * .80:.0f})}}{escaped}")
    return bilingual_header + source_events + "\n".join(lines) + ("\n" if lines else "")


def source_track_to_vtt(cues: list[SubtitleCue]) -> str:
    return cues_to_vtt(cues)


def target_track_to_srt(items: list[dict[str, Any]]) -> str:
    return cues_to_srt([SubtitleCue(id=str(item["id"]), start_time=float(item["start_time"]), end_time=float(item["end_time"]), text=str(item["target_text"])) for item in items])


def target_track_to_vtt(items: list[dict[str, Any]]) -> str:
    return cues_to_vtt([SubtitleCue(id=str(item["id"]), start_time=float(item["start_time"]), end_time=float(item["end_time"]), text=str(item["target_text"])) for item in items])
