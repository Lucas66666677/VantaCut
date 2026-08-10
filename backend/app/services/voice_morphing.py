"""Consent-bound RVC orchestration that carries original pitch and dynamics forward."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings


class VoiceMorphError(RuntimeError):
    pass


@dataclass(frozen=True)
class MorphCharacter:
    id: str
    label: str
    emoji: str
    model_path: str | None
    pitch_shift_semitones: float


def characters() -> dict[str, MorphCharacter]:
    # These are fictional timbres, never names/likenesses of real people.
    return {
        "robot": MorphCharacter("robot", "機器人", "🤖", settings.rvc_robot_model_path, -1.0),
        "monster": MorphCharacter("monster", "怪獸", "👹", settings.rvc_monster_model_path, -4.0),
        "storybook": MorphCharacter("storybook", "童話童聲", "👧", settings.rvc_storybook_model_path, 3.0),
    }


def extract_prosody(input_wav: str | Path, *, f0_destination: str | Path, envelope_destination: str | Path) -> dict[str, Any]:
    """Extract F0 and RMS envelope; unvoiced frames remain null for breaths/laughter texture."""
    try:
        import librosa
        import numpy as np
    except ImportError as exc:
        raise VoiceMorphError("librosa and NumPy are required for voice morphing") from exc
    samples, sample_rate = librosa.load(str(input_wav), sr=24000, mono=True)
    if len(samples) < int(sample_rate * .15):
        raise VoiceMorphError("The selected audio range is too short to morph")
    hop, frame = 256, 1024
    f0 = librosa.yin(samples, fmin=55, fmax=1100, sr=sample_rate, frame_length=frame, hop_length=hop)
    rms = librosa.feature.rms(y=samples, frame_length=frame, hop_length=hop)[0]
    threshold = float(np.percentile(rms, 18))
    f0_payload = {"sample_rate": sample_rate, "hop_length": hop, "f0_hz": [round(float(value), 3) if rms[index] > threshold else None for index, value in enumerate(f0)]}
    envelope_payload = {"sample_rate": sample_rate, "hop_length": hop, "rms": [round(float(value), 6) for value in rms], "voiced_threshold": round(threshold, 6)}
    Path(f0_destination).write_text(json.dumps(f0_payload, separators=(",", ":")), encoding="utf-8")
    Path(envelope_destination).write_text(json.dumps(envelope_payload, separators=(",", ":")), encoding="utf-8")
    return {"sample_rate": sample_rate, "hop_length": hop, "frame_count": len(rms), "voiced_ratio": round(sum(value is not None for value in f0_payload["f0_hz"]) / max(1, len(f0_payload["f0_hz"])), 3)}


def build_rvc_command(*, input_wav: str, output_wav: str, character: MorphCharacter, f0_json: str, envelope_json: str) -> list[str]:
    if not settings.rvc_convert_command:
        raise VoiceMorphError("RVC_CONVERT_COMMAND must be configured on the GPU worker")
    if not character.model_path:
        raise VoiceMorphError(f"No RVC model is configured for the {character.id} character")
    rendered = settings.rvc_convert_command.format(input=input_wav, output=output_wav, model=character.model_path, f0_json=f0_json, envelope_json=envelope_json, pitch_shift=character.pitch_shift_semitones)
    # Command is configured by trusted operators, never composed from user input.
    import shlex
    return shlex.split(rendered)


def extract_audio_range(*, input_path: str, output_wav: str, start: float, end: float) -> None:
    if start < 0 or end <= start:
        raise VoiceMorphError("Invalid voice morph source range")
    try:
        result = subprocess.run(["ffmpeg", "-y", "-ss", f"{start:.6f}", "-t", f"{end - start:.6f}", "-i", input_path, "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", output_wav], check=False, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise VoiceMorphError("Audio extraction timed out") from exc
    if result.returncode:
        raise VoiceMorphError(f"Audio extraction failed: {result.stderr[-1000:]}")


def fit_morph_duration(*, input_wav: str, output_wav: str, duration: float) -> None:
    """Only pad/trim to source duration; never time-stretch the captured performance."""
    try:
        result = subprocess.run(["ffmpeg", "-y", "-i", input_wav, "-af", f"apad,atrim=duration={duration:.6f},afade=t=in:st=0:d=.02,afade=t=out:st={max(.02, duration - .04):.6f}:d=.04", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", output_wav], check=False, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise VoiceMorphError("Voice-morph alignment timed out") from exc
    if result.returncode:
        raise VoiceMorphError(f"Voice-morph alignment failed: {result.stderr[-1000:]}")
