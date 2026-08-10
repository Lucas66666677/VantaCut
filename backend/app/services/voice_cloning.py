"""Reference-clip quality scoring and encryption utilities for consented voice cloning."""
from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings


class VoiceCloningError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReferenceClip:
    start_seconds: float
    duration_seconds: float
    quality_score: float
    metrics: dict[str, float]


def choose_clean_reference(wav_path: str | Path, *, duration_seconds: float = 4.0, stride_seconds: float = .5) -> ReferenceClip:
    """Choose a 3–5 s speaking window using energy, activity, clipping and noise-floor heuristics."""
    try:
        import numpy as np
    except ImportError as exc:
        raise VoiceCloningError("NumPy is required for voice reference selection") from exc
    with wave.open(str(wav_path), "rb") as source:
        sample_rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        if width != 2:
            raise VoiceCloningError("Reference selection expects 16-bit PCM WAV")
        raw = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if channels > 1:
        raw = raw.reshape(-1, channels).mean(axis=1)
    required = int(sample_rate * duration_seconds)
    if len(raw) < int(sample_rate * 3):
        raise VoiceCloningError("At least 3 seconds of audio is required to create a voice profile")
    required = min(required, len(raw))
    step = max(1, int(sample_rate * stride_seconds))
    best: ReferenceClip | None = None
    for start in range(0, len(raw) - required + 1, step):
        samples = raw[start:start + required]
        amplitude = np.abs(samples)
        rms = float(np.sqrt(np.mean(samples * samples)))
        floor, peak = float(np.percentile(amplitude, 20)), float(np.percentile(amplitude, 95))
        activity = float(np.mean(amplitude > max(.012, floor * 2.2)))
        clipping = float(np.mean(amplitude > .985))
        snr_proxy = min(1.0, max(0.0, (peak - floor) / max(peak, 1e-5)))
        # Penalise near-silence, clipped shouting, and windows with too little voiced activity.
        loudness = min(1.0, rms / .12)
        score = max(0.0, min(1.0, .32 * loudness + .33 * activity + .35 * snr_proxy - 1.5 * clipping))
        candidate = ReferenceClip(start / sample_rate, required / sample_rate, score, {"rms": rms, "activity_ratio": activity, "snr_proxy": snr_proxy, "clipping_ratio": clipping})
        if best is None or candidate.quality_score > best.quality_score:
            best = candidate
    if best is None:
        raise VoiceCloningError("Could not locate a usable voice reference window")
    if best.quality_score < .28:
        raise VoiceCloningError("No sufficiently clean speech was found; upload a clearer 3–5 second recording")
    return best


def extract_reference_audio(source_wav: str | Path, output_wav: str | Path, clip: ReferenceClip) -> None:
    """Apply conservative voice-band cleanup; this is a reference clip, not a destructive mix edit."""
    try:
        result = subprocess.run([
            "ffmpeg", "-y", "-ss", f"{clip.start_seconds:.3f}", "-t", f"{clip.duration_seconds:.3f}", "-i", str(source_wav),
            "-af", "highpass=f=70,lowpass=f=9000,afftdn=nr=6", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output_wav),
        ], check=False, capture_output=True, text=True, timeout=10 * 60)
    except subprocess.TimeoutExpired as exc:
        raise VoiceCloningError("Voice-reference extraction timed out") from exc
    if result.returncode:
        raise VoiceCloningError(f"Voice-reference extraction failed: {result.stderr[-1000:]}")


def wav_duration_seconds(path: str | Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / max(source.getframerate(), 1)


def fit_replacement_to_slot(source_wav: str | Path, output_wav: str | Path, *, slot_duration: float) -> dict[str, float]:
    """Tempo-fit, pad/trim, and add tiny fades so the generated clip lands precisely in its cue slot."""
    if slot_duration <= .05:
        raise VoiceCloningError("Replacement slot is too short")
    original_duration = wav_duration_seconds(source_wav)
    raw_rate = original_duration / slot_duration
    # Avoid excessive time-stretch artifacts. Remaining difference is padded or trimmed with a short fade.
    applied_rate = min(1.25, max(.8, raw_rate))
    fade_out_start = max(.01, slot_duration - .04)
    filter_graph = f"atempo={applied_rate:.6f},afade=t=in:st=0:d=0.025,apad,atrim=duration={slot_duration:.6f},afade=t=out:st={fade_out_start:.6f}:d=0.04"
    try:
        result = subprocess.run([
            "ffmpeg", "-y", "-i", str(source_wav), "-af", filter_graph, "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output_wav),
        ], check=False, capture_output=True, text=True, timeout=10 * 60)
    except subprocess.TimeoutExpired as exc:
        raise VoiceCloningError("Voice replacement fitting timed out") from exc
    if result.returncode:
        raise VoiceCloningError(f"Voice replacement fitting failed: {result.stderr[-1000:]}")
    return {"source_duration": original_duration, "slot_duration": slot_duration, "alignment_atempo": applied_rate}


def build_voice_replacement_mix_command(input_video: str, replacements: list[dict[str, Any]], output_video: str) -> list[str]:
    """Replace timed mixed-audio cue slots with generated mono WAVs.

    For stem-enabled projects this can be upgraded to mute only the Dialogue stem. The conservative
    default mutes the complete source mix during the short replacement slot, preventing doubled words.
    """
    command = ["ffmpeg", "-y", "-i", input_video]
    filters: list[str] = []
    current = "basea"
    filters.append("[0:a]asetpts=PTS-STARTPTS[basea]")
    for index, replacement in enumerate(replacements):
        command.extend(["-i", str(replacement["local_path"])])
        start, end = float(replacement["start_time"]), float(replacement["end_time"])
        muted, delayed, mixed = f"muteda{index}", f"replacea{index}", f"mixeda{index}"
        filters.append(f"[{current}]volume=volume=0:enable='between(t\\,{start:.6f}\\,{end:.6f})'[{muted}]")
        filters.append(f"[{index + 1}:a]adelay={int(round(start * 1000))}:all=1,asetpts=PTS-STARTPTS[{delayed}]")
        filters.append(f"[{muted}][{delayed}]amix=inputs=2:duration=first:normalize=0[{mixed}]")
        current = mixed
    return command + ["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", f"[{current}]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output_video]


class VoiceProfileCipher:
    """Encrypt cached XTTS latent vectors at rest; the API never receives their plaintext."""

    def __init__(self) -> None:
        key = settings.voice_profile_encryption_key
        if not key:
            if settings.use_mock_ai:
                self._fernet = None
                return
            raise VoiceCloningError("VOICE_PROFILE_ENCRYPTION_KEY is required for production voice cloning")
        try:
            from cryptography.fernet import Fernet
            self._fernet = Fernet(key.encode("utf-8"))
        except (ImportError, ValueError, TypeError) as exc:
            raise VoiceCloningError("VOICE_PROFILE_ENCRYPTION_KEY must be a valid Fernet key") from exc

    def encrypt(self, payload: bytes) -> bytes:
        return self._fernet.encrypt(payload) if self._fernet else payload

    def decrypt(self, payload: bytes) -> bytes:
        return self._fernet.decrypt(payload) if self._fernet else payload
