"""XTTS-v2 voice-cloning adapter. Imported lazily so API/web workers need no GPU runtime."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import wave

from app.ai.providers.base import VoiceCloneProvider
from app.core.config import settings


class VoiceCloneProviderError(RuntimeError):
    pass


class XTTSVoiceProvider(VoiceCloneProvider):
    """Caches XTTS conditioning latents for one consented project voice profile."""

    _EMOTION_PARAMETERS = {
        "neutral": {"temperature": .65, "length_penalty": 1.0, "repetition_penalty": 2.0},
        "excited": {"temperature": .78, "length_penalty": .88, "repetition_penalty": 2.1},
        "calm": {"temperature": .52, "length_penalty": 1.08, "repetition_penalty": 2.0},
        "serious": {"temperature": .55, "length_penalty": 1.02, "repetition_penalty": 2.1},
        "warm": {"temperature": .68, "length_penalty": 1.03, "repetition_penalty": 1.95},
        "sad": {"temperature": .58, "length_penalty": 1.10, "repetition_penalty": 2.05},
    }

    def __init__(self) -> None:
        self._model: Any | None = None
        self._torch: Any | None = None

    @property
    def name(self) -> str:
        return "xtts_v2"

    def _load(self) -> tuple[Any, Any]:
        if self._model is not None and self._torch is not None:
            return self._model, self._torch
        if not settings.xtts_model_dir:
            raise VoiceCloneProviderError("XTTS_MODEL_DIR must point to the downloaded XTTS-v2 model directory")
        try:
            import torch
            from TTS.tts.configs.xtts_config import XttsConfig
            from TTS.tts.models.xtts import Xtts
        except ImportError as exc:
            raise VoiceCloneProviderError("Install backend/requirements.xtts.txt on the GPU worker") from exc
        model_dir = Path(settings.xtts_model_dir)
        config_path = Path(settings.xtts_config_path) if settings.xtts_config_path else model_dir / "config.json"
        if not model_dir.is_dir() or not config_path.is_file():
            raise VoiceCloneProviderError("XTTS model directory/config.json is unavailable")
        config = XttsConfig(); config.load_json(str(config_path))
        model = Xtts.init_from_config(config)
        model.load_checkpoint(config, checkpoint_dir=str(model_dir), use_deepspeed=settings.xtts_use_deepspeed)
        device = settings.xtts_device
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise VoiceCloneProviderError("XTTS_DEVICE requests CUDA but no CUDA device is available")
        model.to(device)
        self._model, self._torch = model, torch
        return model, torch

    def extract_profile(self, reference_wav: str, artifact_path: str) -> dict[str, Any]:
        model, torch = self._load()
        latent, embedding = model.get_conditioning_latents(audio_path=[reference_wav])
        torch.save({"gpt_cond_latent": latent.detach().cpu(), "speaker_embedding": embedding.detach().cpu()}, artifact_path)
        return {"provider": self.name, "speaker_embedding_dimensions": list(embedding.shape), "conditioning_dimensions": list(latent.shape)}

    def synthesize(self, *, text: str, language: str, artifact_path: str, output_wav: str, emotion: str = "neutral", tempo: float = 1.0) -> dict[str, Any]:
        if not .8 <= tempo <= 1.25:
            raise VoiceCloneProviderError("tempo must be in [0.8, 1.25]")
        model, torch = self._load()
        artifact = torch.load(artifact_path, map_location=settings.xtts_device, weights_only=True)
        parameters = self._EMOTION_PARAMETERS.get(emotion, self._EMOTION_PARAMETERS["neutral"])
        result = model.inference(text, language, artifact["gpt_cond_latent"].to(settings.xtts_device), artifact["speaker_embedding"].to(settings.xtts_device), speed=tempo, **parameters)
        try:
            import torchaudio
        except ImportError as exc:
            raise VoiceCloneProviderError("torchaudio is required for XTTS WAV output") from exc
        torchaudio.save(output_wav, torch.tensor(result["wav"]).unsqueeze(0).cpu(), 24000)
        return {"provider": self.name, "emotion": emotion, "tempo": tempo, "sample_rate": 24000, "style_control": "best_effort_sampling_parameters"}


class MockVoiceCloneProvider(VoiceCloneProvider):
    """Development-only deterministic WAV substitute; it never imitates a real person."""

    @property
    def name(self) -> str:
        return "mock_voice_clone"

    def extract_profile(self, reference_wav: str, artifact_path: str) -> dict[str, Any]:
        Path(artifact_path).write_bytes(b"mock-voice-profile")
        return {"provider": self.name, "speaker_embedding_dimensions": [0], "reference": Path(reference_wav).name}

    def synthesize(self, *, text: str, language: str, artifact_path: str, output_wav: str, emotion: str = "neutral", tempo: float = 1.0) -> dict[str, Any]:
        del artifact_path, language, emotion
        import math
        sample_rate, seconds = 24000, max(.25, min(8, len(text) / 12 / tempo))
        frames = int(sample_rate * seconds)
        with wave.open(output_wav, "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(sample_rate)
            output.writeframes(b"".join(int(1700 * math.sin(2 * math.pi * 220 * index / sample_rate)).to_bytes(2, "little", signed=True) for index in range(frames)))
        return {"provider": self.name, "tempo": tempo, "sample_rate": sample_rate, "development_only": True}
