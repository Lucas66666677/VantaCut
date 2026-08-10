"""Academic glossary preservation, narrative-plan validation, LUT, and conservative delivery processing."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.schemas.academic import AcademicGlossaryEntry, AcademicNarrativePlan
from app.schemas.subtitle import SubtitleCue


class AcademicModeError(RuntimeError):
    pass


def _replace_exact(text: str, entry: AcademicGlossaryEntry) -> tuple[str, bool]:
    candidates = sorted({entry.term, *entry.aliases}, key=len, reverse=True)
    changed = False
    for candidate in candidates:
        if not candidate:
            continue
        boundary = r"(?<!\w)" if re.search(r"[A-Za-z0-9]", candidate) else ""
        tail = r"(?!\w)" if re.search(r"[A-Za-z0-9]", candidate) else ""
        updated, count = re.subn(boundary + re.escape(candidate) + tail, entry.term, text, flags=0 if entry.case_sensitive else re.IGNORECASE)
        text, changed = updated, changed or count > 0
    return text, changed


def canonicalise_glossary_text(text: str, glossary: list[AcademicGlossaryEntry]) -> tuple[str, list[dict[str, Any]]]:
    corrections: list[dict[str, Any]] = []
    for entry in glossary:
        updated, changed = _replace_exact(text, entry)
        if changed:
            corrections.append({"term": entry.term, "mode": "exact_or_alias", "review_required": False})
        text = updated
    # Surface likely phonetic/spacing errors, but never alter them silently.
    words = re.findall(r"[\w.-]+", text.lower())
    for entry in glossary:
        normal = re.sub(r"\W", "", entry.term.lower())
        if normal and words and max(SequenceMatcher(None, normal, re.sub(r"\W", "", word)).ratio() for word in words) >= .82 and entry.term.lower() not in text.lower():
            corrections.append({"term": entry.term, "mode": "fuzzy_candidate", "review_required": True})
    return text, corrections


def apply_glossary_to_cues(cues: list[SubtitleCue], glossary: list[AcademicGlossaryEntry]) -> tuple[list[SubtitleCue], list[dict[str, Any]]]:
    review: list[dict[str, Any]] = []
    for cue in cues:
        cue.text, corrections = canonicalise_glossary_text(cue.text, glossary)
        for word in cue.words:
            word.word, word_corrections = canonicalise_glossary_text(word.word, glossary)
            corrections.extend(word_corrections)
        review.extend({"cue_id": cue.id, **item} for item in corrections)
    return cues, review


def academic_lut(destination: str | Path, *, size: int = 33) -> Path:
    """A restrained neutral/cool academic look: lower saturation, gentle contrast, protected highlights."""
    if size not in {17, 33, 65}:
        raise ValueError("LUT size must be 17, 33, or 65")
    values = [index / (size - 1) for index in range(size)]; lines = ['TITLE "Academic Neutral Research Look"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0", ""]
    for blue in values:
        for green in values:
            for red in values:
                luma = .2126 * red + .7152 * green + .0722 * blue
                saturation = .76
                r, g, b = (luma + (red - luma) * saturation) * .985, (luma + (green - luma) * saturation) * .995, (luma + (blue - luma) * saturation) * 1.025
                r, g, b = [max(0.0, min(1.0, (value - .5) * 1.08 + .5)) for value in (r, g, b)]
                lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
    output = Path(destination); output.write_text("\n".join(lines) + "\n", encoding="utf-8"); return output


def validate_narrative_plan(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        plan = AcademicNarrativePlan.model_validate(raw)
    except Exception as exc:
        raise AcademicModeError("Text provider returned an invalid academic narrative plan") from exc
    expected = ["motivation", "methodology", "results", "future_works"]
    if [section.kind for section in plan.sections] != expected:
        raise AcademicModeError("Academic narrative sections must be ordered motivation, methodology, results, future_works")
    return plan.model_dump(mode="json")


def academic_delivery_command(input_path: str, output_path: str, *, tempo: float) -> list[str]:
    if not .90 <= tempo <= 1.03:
        raise AcademicModeError("Academic speech tempo must be between 0.90 and 1.03")
    # This changes video and audio by the same conservative factor, preserving A/V sync.
    graph = f"[0:v]setpts=PTS/{tempo:.6f}[outv];[0:a]highpass=f=70,lowpass=f=12000,equalizer=f=180:t=q:w=1.1:g=1.3,equalizer=f=3400:t=q:w=1.0:g=1.0,acompressor=threshold=0.09:ratio=2.3:attack=20:release=180:makeup=1.1,atempo={tempo:.6f}[outa]"
    return ["ffmpeg", "-y", "-i", input_path, "-filter_complex", graph, "-map", "[outv]", "-map", "[outa]", "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart", output_path]
