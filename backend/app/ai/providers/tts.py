"""Built-in-voice narration providers; deliberately separate from voice cloning."""
from __future__ import annotations

import math
import os
import wave
from pathlib import Path
from typing import Any

import httpx

from app.ai.providers.base import NarrationTTSProvider


class NarrationTTSError(RuntimeError):
    pass


class OpenAINarrationTTSProvider(NarrationTTSProvider):
    def __init__(self, *, api_key: str | None, model: str) -> None:
        self.api_key, self.model = api_key, model

    @property
    def name(self) -> str:
        return "openai_tts"

    def synthesize_narration(self, *, text: str, voice: str, instructions: str, speed: float, output_wav: str) -> dict[str, Any]:
        if not self.api_key:
            raise NarrationTTSError("OPENAI_API_KEY is required for OpenAI TTS")
        if not .25 <= speed <= 4:
            raise NarrationTTSError("TTS speed must be in [0.25, 4]")
        try:
            response = httpx.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "voice": voice, "input": text, "instructions": instructions, "speed": speed, "response_format": "wav"},
                timeout=180,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            detail = exc.response.text[-1000:] if isinstance(exc, httpx.HTTPStatusError) else str(exc)
            raise NarrationTTSError(f"OpenAI TTS request failed: {detail}") from exc
        Path(output_wav).write_bytes(response.content)
        return {"provider": self.name, "model": self.model, "voice": voice, "speed": speed, "instructions_applied": bool(instructions)}


class MockNarrationTTSProvider(NarrationTTSProvider):
    @property
    def name(self) -> str:
        return "mock_narration_tts"

    def synthesize_narration(self, *, text: str, voice: str, instructions: str, speed: float, output_wav: str) -> dict[str, Any]:
        del instructions
        sample_rate, duration = 24_000, max(.35, min(120, len(text) / 5.2 / speed))
        frequency = {"coral": 264, "sage": 178, "ballad": 206, "shimmer": 238, "onyx": 156}.get(voice, 220)
        frames = int(sample_rate * duration)
        with wave.open(output_wav, "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(sample_rate)
            output.writeframes(b"".join(int(1500 * math.sin(2 * math.pi * frequency * frame / sample_rate)).to_bytes(2, "little", signed=True) for frame in range(frames)))
        return {"provider": self.name, "voice": voice, "speed": speed, "development_only": True}
