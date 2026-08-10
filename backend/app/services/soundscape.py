"""Visual sound-event planning and pluggable foley generation."""
from __future__ import annotations

import os
import shlex
import subprocess
import wave
from abc import ABC, abstractmethod
from pathlib import Path

from app.ai.providers.factory import get_vision_provider
from app.ai.soundscape_prompts import SOUNDSCAPE_SYSTEM_PROMPT, SOUNDSCAPE_USER_PROMPT, soundscape_response_schema
from app.schemas.soundscape import SoundscapeEvent, SoundscapePlan


class SoundscapeError(RuntimeError):
    pass


class FoleyGenerationProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, event: SoundscapeEvent, output_path: Path) -> None:
        """Create a mono 48kHz PCM16 WAV file exactly matching the event duration."""


class ProceduralFoleyProvider(FoleyGenerationProvider):
    """No-cost fallback for development and a safe bed when a neural generator is unavailable."""

    name = "procedural_foley"

    def generate(self, event: SoundscapeEvent, output_path: Path) -> None:
        import numpy as np

        rate = 48_000
        duration = event.end_time - event.start_time
        count = max(1, round(duration * rate))
        rng = np.random.default_rng(abs(hash(event.id)) % (2**32))
        noise = rng.normal(0, 1, count).astype(np.float32)
        if event.kind == "wind":
            signal = np.concatenate(([0.0], np.diff(noise))) * .035 + np.sin(np.arange(count) * 2 * np.pi * 48 / rate) * .02
        elif event.kind == "footsteps":
            signal = noise * .004
            for offset in range(rate // 5, count, max(rate // 2, 1)):
                length = min(rate // 12, count - offset)
                signal[offset:offset + length] += np.exp(-np.arange(length) / (rate * .018)) * .25
        elif event.kind in {"water", "traffic", "ambient", "room_tone"}:
            signal = noise * .018 + np.sin(np.arange(count) * 2 * np.pi * 63 / rate) * .008
        else:
            signal = noise * .012
        with wave.open(str(output_path), "wb") as writer:
            writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(rate)
            writer.writeframes((signal.clip(-.95, .95) * 32767).astype("<i2").tobytes())


class CommandFoleyProvider(FoleyGenerationProvider):
    """Adapter for AudioLDM/Stable Audio-compatible local wrappers provisioned by operations."""

    name = "command_audio_generation"

    def __init__(self, template: str) -> None:
        self.template = template

    def generate(self, event: SoundscapeEvent, output_path: Path) -> None:
        required = {"{prompt}", "{duration}", "{output}"}
        if not required.issubset(set(__import__("re").findall(r"\{[^}]+\}", self.template))):
            raise SoundscapeError("AUDIO_GENERATION_COMMAND must contain {prompt}, {duration}, and {output}")
        command = shlex.split(self.template.format(prompt=event.generation_prompt, duration=event.end_time - event.start_time, output=str(output_path)))
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=30 * 60)
        except subprocess.TimeoutExpired as exc:
            raise SoundscapeError("Audio generation timed out") from exc
        except subprocess.CalledProcessError as exc:
            raise SoundscapeError((exc.stderr or "Audio generation failed")[-2000:]) from exc
        if not output_path.exists():
            raise SoundscapeError("Audio generator did not create its output WAV")


def get_foley_provider() -> FoleyGenerationProvider:
    command = os.getenv("AUDIO_GENERATION_COMMAND", "")
    return CommandFoleyProvider(command) if command else ProceduralFoleyProvider()


def plan_soundscape(video_uri: str, sampled_frames: list[dict[str, object]], *, output_duration: float) -> SoundscapePlan:
    raw = get_vision_provider().analyze_video(
        video_uri,
        f"{SOUNDSCAPE_SYSTEM_PROMPT}\n\n{SOUNDSCAPE_USER_PROMPT}",
        response_schema=soundscape_response_schema(),
        context={"task": "soundscape_planning", "sampled_frames": sampled_frames, "output_duration": output_duration},
    )
    try:
        plan = SoundscapePlan.model_validate(raw)
    except Exception as exc:
        raise SoundscapeError("Multimodal provider returned invalid soundscape JSON") from exc
    return SoundscapePlan(events=[event for event in plan.events if event.end_time <= output_duration])
