"""Provider adapters for licensed Suno/Udio-compatible music generation gateways."""
from __future__ import annotations

import math
import time
import wave
from pathlib import Path
from typing import Any

import httpx

from app.ai.providers.base import MusicGenerationProvider


class MockMusicGenerationProvider(MusicGenerationProvider):
    """Offline development fallback: creates a harmless fading stereo tone, never a copied song."""

    @property
    def name(self) -> str:
        return "mock_music_generation"

    def generate_music(self, *, prompt: str, duration_seconds: float, instrumental: bool, output_path: str) -> dict[str, Any]:
        del prompt, instrumental
        sample_rate, seconds = 44100, max(1.0, duration_seconds)
        frame_count = int(sample_rate * seconds); fade_frames = min(frame_count, int(sample_rate * 2))
        with wave.open(output_path, "wb") as stream:
            stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(sample_rate)
            for offset in range(0, frame_count, 8192):
                payload = bytearray()
                for index in range(offset, min(frame_count, offset + 8192)):
                    envelope = min(1.0, (frame_count - index) / max(1, fade_frames))
                    sample = int(2500 * envelope * math.sin(2 * math.pi * 220 * index / sample_rate))
                    payload.extend(sample.to_bytes(2, "little", signed=True) * 2)
                stream.writeframes(payload)
        return {"provider": self.name, "has_vocals": False, "duration_seconds": seconds, "development_placeholder": True}


class GatewayMusicGenerationProvider(MusicGenerationProvider):
    """Adapter for a provider-approved Suno/Udio gateway, not an undocumented consumer endpoint."""

    def __init__(self, *, provider: str, api_key: str | None, base_url: str | None, timeout_seconds: int, poll_seconds: float) -> None:
        self.provider, self.api_key, self.base_url = provider, api_key, (base_url or "").rstrip("/")
        self.timeout_seconds, self.poll_seconds = timeout_seconds, poll_seconds

    @property
    def name(self) -> str:
        return self.provider

    def generate_music(self, *, prompt: str, duration_seconds: float, instrumental: bool, output_path: str) -> dict[str, Any]:
        if not self.api_key or not self.base_url:
            raise RuntimeError(f"{self.provider} music gateway requires MUSIC_GENERATION_API_KEY and MUSIC_GENERATION_BASE_URL")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=45, follow_redirects=True) as client:
            created = client.post(f"{self.base_url}/generate", headers=headers, json={"prompt": prompt, "duration_seconds": duration_seconds, "instrumental": instrumental, "provider": self.provider}).raise_for_status().json()
            deadline, result = time.monotonic() + self.timeout_seconds, created
            while str(result.get("status", "completed")).lower() not in {"completed", "succeeded", "ready"}:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Music generation provider timed out")
                job_id = result.get("id") or result.get("job_id")
                if not job_id:
                    raise RuntimeError("Music gateway response lacks job id")
                time.sleep(self.poll_seconds)
                result = client.get(f"{self.base_url}/jobs/{job_id}", headers=headers).raise_for_status().json()
            audio_url = result.get("audio_url") or result.get("url")
            if not isinstance(audio_url, str) or not audio_url.startswith(("https://", "http://")):
                raise RuntimeError("Music gateway response lacks a trusted audio_url")
            response = client.get(audio_url); response.raise_for_status(); Path(output_path).write_bytes(response.content)
        return {"provider": self.provider, "provider_job_id": result.get("id") or result.get("job_id"), "has_vocals": bool(result.get("has_vocals", not instrumental)), "duration_seconds": result.get("duration_seconds")}
