"""WCAG-oriented audio-description planning, narration assembly, and delivery muxing."""
from __future__ import annotations

import math
import re
import shlex
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings


class AudioDescriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioGap:
    source_start: float
    source_end: float
    output_start: float
    output_end: float
    audio_context: str

    @property
    def duration(self) -> float:
        return self.output_end - self.output_start


def main_keep_segments(confirmed: dict[str, Any]) -> list[dict[str, float]]:
    """Return source ranges in final-output order, supporting legacy and multi-track timelines."""
    tracks = confirmed.get("tracks", [])
    raw = next((track.get("clips", []) for track in tracks if track.get("type") == "main_video"), None) if isinstance(tracks, list) else None
    raw = raw if isinstance(raw, list) else confirmed.get("segments", [])
    segments: list[dict[str, float]] = []
    output_start = 0.0
    for clip in raw if isinstance(raw, list) else []:
        if not isinstance(clip, dict) or clip.get("action", "keep") != "keep":
            continue
        start, end = float(clip.get("source_start", 0)), float(clip.get("source_end", 0))
        if end <= start:
            continue
        segments.append({"source_start": start, "source_end": end, "output_start": output_start, "output_end": output_start + end - start})
        output_start += end - start
    return segments


def transcript_gaps(
    transcript: dict[str, Any],
    keep_segments: list[dict[str, float]],
    *,
    min_gap_seconds: float,
    source_duration: float,
    silences: list[dict[str, Any]] | None = None,
) -> list[AudioGap]:
    """Find dialogue-free ranges and map only retained source portions to output time."""
    speech = sorted(
        (float(item.get("start", 0)), float(item.get("end", 0)))
        for item in transcript.get("segments", []) if isinstance(item, dict) and float(item.get("end", 0)) > float(item.get("start", 0))
    )
    merged: list[list[float]] = []
    for start, end in speech:
        if merged and start <= merged[-1][1] + .05:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    raw_gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start - cursor >= min_gap_seconds:
            raw_gaps.append((cursor, start))
        cursor = max(cursor, end)
    if source_duration - cursor >= min_gap_seconds:
        raw_gaps.append((cursor, source_duration))
    silence_ranges = [(float(item.get("start", 0)), float(item.get("end", 0))) for item in silences or [] if isinstance(item, dict)]
    output: list[AudioGap] = []
    for gap_start, gap_end in raw_gaps:
        for kept in keep_segments:
            start, end = max(gap_start, kept["source_start"]), min(gap_end, kept["source_end"])
            if end - start < min_gap_seconds:
                continue
            output_start = kept["output_start"] + (start - kept["source_start"])
            output_end = output_start + (end - start)
            is_silent = any(s <= start + .05 and e >= end - .05 for s, e in silence_ranges)
            output.append(AudioGap(start, end, output_start, output_end, "silence" if is_silent else "music_or_ambient"))
    return output


def description_limits(duration_seconds: float, language: str) -> dict[str, int]:
    # Leave a short lead/tail so a description never collides with the next spoken word.
    usable = max(0.0, duration_seconds - .35)
    max_words = max(1, int(math.floor(usable * settings.audio_description_words_per_second)))
    # CJK narration has no reliable whitespace word boundaries; use an equivalent character ceiling.
    max_characters = max(4, int(math.floor(usable * (4.2 if language.lower().startswith(("zh", "ja", "ko")) else 6.0))))
    return {"max_words": max_words, "max_characters": max_characters}


AUDIO_DESCRIPTION_SYSTEM_PROMPT = """You write concise, neutral audio descriptions for blind and low-vision viewers.
Describe only visually essential information that is not already conveyed by dialogue: meaningful actions,
scene/location changes, on-screen text, and relevant facial expression or gesture. Do not invent names,
motives, emotions, or events that are not visible. Do not repeat dialogue. The narration must fit entirely
in the supplied dialogue-free interval. Return JSON only, matching the supplied schema exactly."""


def build_description_prompt(*, gap: AudioGap, language: str, limits: dict[str, int]) -> str:
    return f"""Analyze the supplied video excerpt for this exact source interval: {gap.source_start:.3f}s–{gap.source_end:.3f}s.
The interval is {gap.duration:.2f}s and contains {gap.audio_context}, not dialogue.
Write one concise audio-description line in {language}. It must be factual and fit naturally in the gap.
Hard limits: no more than {limits['max_words']} whitespace-delimited words and no more than {limits['max_characters']} characters for CJK text.
Return: {{\"description\": string, \"visual_focus\": string, \"word_count\": integer}}."""


def validate_description(payload: dict[str, Any], *, limits: dict[str, int], language: str) -> str:
    text = re.sub(r"\s+", " ", str(payload.get("description", "")).strip())
    if not text:
        raise AudioDescriptionError("Vision provider returned an empty audio description")
    cjk = language.lower().startswith(("zh", "ja", "ko"))
    unit_count = len(re.sub(r"\s", "", text)) if cjk else len(text.split())
    ceiling = limits["max_characters"] if cjk else limits["max_words"]
    if unit_count > ceiling:
        raise AudioDescriptionError(f"Generated description exceeds its {ceiling}-unit gap limit")
    return text


def extract_visual_excerpt(source_video: Path, target: Path, *, start: float, end: float) -> None:
    try:
        result = subprocess.run([
            "ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(source_video),
            "-an", "-vf", "fps=2,scale='min(960,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", str(target),
        ], capture_output=True, text=True, timeout=10 * 60)
    except subprocess.TimeoutExpired as exc:
        raise AudioDescriptionError("Visual excerpt extraction timed out") from exc
    if result.returncode:
        raise AudioDescriptionError(f"Visual excerpt extraction failed: {result.stderr[-1600:]}")


def synthesize_description(*, text: str, language: str, output_wav: Path) -> dict[str, Any]:
    """Use a configured neutral TTS command; mock mode creates an intentionally non-speech dev cue."""
    if settings.use_mock_ai:
        import math as _math
        sample_rate, seconds = 24000, max(.25, min(6.0, len(text) / 8.0))
        with wave.open(str(output_wav), "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(sample_rate)
            output.writeframes(b"".join(int(1100 * _math.sin(2 * _math.pi * 330 * index / sample_rate)).to_bytes(2, "little", signed=True) for index in range(int(seconds * sample_rate))))
        return {"provider": "mock_non_speech_cue", "development_only": True, "sample_rate": sample_rate}
    template = settings.audio_description_tts_command
    if not template or "{text}" not in template or "{output}" not in template:
        raise AudioDescriptionError("AUDIO_DESCRIPTION_TTS_COMMAND must contain {text} and {output} placeholders")
    command = shlex.split(template.format(text=text, output=str(output_wav), language=language))
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=settings.audio_description_tts_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise AudioDescriptionError("Audio-description TTS timed out") from exc
    if result.returncode or not output_wav.exists():
        raise AudioDescriptionError(f"Audio-description TTS failed: {(result.stderr or result.stdout)[-1600:]}")
    return {"provider": "external_tts_command", "sample_rate": 24000}


def fit_description_to_gap(source_wav: Path, output_wav: Path, *, duration_seconds: float) -> None:
    # A conservative atempo range protects intelligibility; excess narration must be regenerated shorter.
    with wave.open(str(source_wav), "rb") as source:
        generated_duration = source.getnframes() / max(source.getframerate(), 1)
    rate = generated_duration / duration_seconds
    if rate > 1.25:
        raise AudioDescriptionError("Narration cannot fit the available gap without harming intelligibility")
    # Short narration is naturally padded with room tone/silence; only speeding
    # up a too-long delivery risks intelligibility.
    rate = max(0.80, rate)
    filter_graph = f"atempo={rate:.6f},afade=t=in:st=0:d=0.025,apad,atrim=duration={duration_seconds:.6f},afade=t=out:st={max(.01, duration_seconds - .05):.6f}:d=0.04"
    try:
        result = subprocess.run(["ffmpeg", "-y", "-i", str(source_wav), "-af", filter_graph, "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(output_wav)], capture_output=True, text=True, timeout=10 * 60)
    except subprocess.TimeoutExpired as exc:
        raise AudioDescriptionError("Narration fitting timed out") from exc
    if result.returncode:
        raise AudioDescriptionError(f"Narration fitting failed: {result.stderr[-1600:]}")


def build_audio_description_track(cues: list[dict[str, Any]], *, output_duration: float, output_wav: Path) -> None:
    """Build a full-length silent-bed narration track aligned to the final edit timebase."""
    command = ["ffmpeg", "-y", "-f", "lavfi", "-t", f"{output_duration:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]
    filters = ["[0:a]asetpts=PTS-STARTPTS[bed]"]
    for index, cue in enumerate(cues, start=1):
        command.extend(["-i", str(cue["local_path"])])
        filters.append(f"[{index}:a]adelay={int(round(float(cue['output_start']) * 1000))}:all=1,asetpts=PTS-STARTPTS[narration{index}]")
    labels = "[bed]" + "".join(f"[narration{index}]" for index in range(1, len(cues) + 1))
    filters.append(f"{labels}amix=inputs={len(cues) + 1}:duration=first:normalize=0,alimiter=limit=0.891251[description]")
    command.extend(["-filter_complex", ";".join(filters), "-map", "[description]", "-c:a", "pcm_s16le", str(output_wav)])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60 * 60)
    except subprocess.TimeoutExpired as exc:
        raise AudioDescriptionError("Audio-description track assembly timed out") from exc
    if result.returncode:
        raise AudioDescriptionError(f"Audio-description track assembly failed: {result.stderr[-2000:]}")


def mux_audio_description_track(*, video_path: str, description_audio_path: str, output_path: str, language: str, container: str) -> None:
    """Keep programme audio default, add a selectable ducked programme+narration description track."""
    command = [
        "ffmpeg", "-y", "-i", video_path, "-i", description_audio_path,
        "-filter_complex",
        "[0:a:0]aformat=channel_layouts=stereo[programme];"
        "[1:a:0]aformat=channel_layouts=stereo[narration];"
        "[programme][narration]sidechaincompress=threshold=0.035:ratio=8:attack=15:release=280[ducked];"
        "[ducked][narration]amix=inputs=2:duration=first:normalize=0[ad_mix]",
        "-map", "0:v:0", "-map", "0:a:0", "-map", "[ad_mix]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-metadata:s:a:0", "title=Original Mix", "-metadata:s:a:0", "handler_name=Original Mix",
        "-metadata:s:a:1", "title=Audio Description", "-metadata:s:a:1", "handler_name=Audio Description",
        "-metadata:s:a:1", f"language={language}", "-disposition:a:0", "default", "-disposition:a:1", "0",
    ]
    if container == "mp4":
        command.extend(["-movflags", "+faststart"])
    command.append(output_path)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=2 * 60 * 60)
    except subprocess.TimeoutExpired as exc:
        raise AudioDescriptionError("Audio-description muxing timed out") from exc
    if result.returncode:
        raise AudioDescriptionError(f"Audio-description muxing failed: {result.stderr[-2000:]}")
