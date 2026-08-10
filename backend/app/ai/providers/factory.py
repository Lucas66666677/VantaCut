import os
from functools import lru_cache

from app.ai.providers.base import ASRProvider, EditingAgentProvider, EmbeddingProvider, MultimodalProvider, MusicGenerationProvider, NarrationTTSProvider, TextAnalysisProvider, VoiceCloneProvider
from app.ai.providers.editing_agent import MockEditingAgentProvider, OpenAILangChainEditingAgentProvider
from app.ai.providers.embeddings import MockEmbeddingProvider, OpenCLIPEmbeddingProvider
from app.ai.providers.gemini import GeminiVideoProvider
from app.ai.providers.mock import MockASRProvider, MockMultimodalProvider
from app.ai.providers.openai import OpenAIMultimodalProvider, OpenAIWhisperProvider
from app.ai.providers.voice import MockVoiceCloneProvider, XTTSVoiceProvider
from app.ai.providers.tts import MockNarrationTTSProvider, OpenAINarrationTTSProvider
from app.ai.providers.music import GatewayMusicGenerationProvider, MockMusicGenerationProvider
from app.core.config import settings


def _api_key(provider: str) -> str | None:
    return os.getenv(f"{provider.upper()}_API_KEY")


@lru_cache(maxsize=4)
def get_vision_provider(provider_name: str | None = None) -> MultimodalProvider:
    if settings.use_mock_ai or (provider_name or "").lower() == "mock":
        return MockMultimodalProvider(delay_seconds=settings.mock_ai_delay_seconds)
    name = (provider_name or os.getenv("AI_VISION_PROVIDER", "gemini")).lower()
    if name == "gemini":
        return GeminiVideoProvider(
            api_key=_api_key("gemini"),
            model=os.getenv("GEMINI_VISION_MODEL", "gemini-video-model"),
        )
    if name == "openai":
        return OpenAIMultimodalProvider(
            api_key=_api_key("openai"),
            model=os.getenv("OPENAI_VISION_MODEL", "gpt-4o"),
        )
    raise ValueError(f"Unsupported AI_VISION_PROVIDER: {name}")


@lru_cache(maxsize=4)
def get_asr_provider(provider_name: str | None = None) -> ASRProvider:
    if settings.use_mock_ai or (provider_name or "").lower() == "mock":
        return MockASRProvider(delay_seconds=settings.mock_ai_delay_seconds)
    name = (provider_name or os.getenv("AI_ASR_PROVIDER", "openai_whisper")).lower()
    if name in {"openai", "openai_whisper", "whisper"}:
        return OpenAIWhisperProvider(
            api_key=_api_key("openai"),
            model=os.getenv("OPENAI_ASR_MODEL", "whisper-1"),
        )
    raise ValueError(f"Unsupported AI_ASR_PROVIDER: {name}")


@lru_cache(maxsize=4)
def get_text_provider(provider_name: str | None = None) -> TextAnalysisProvider:
    if settings.use_mock_ai or (provider_name or "").lower() == "mock":
        return MockMultimodalProvider(delay_seconds=settings.mock_ai_delay_seconds)
    name = (provider_name or os.getenv("AI_TEXT_PROVIDER") or os.getenv("AI_VISION_PROVIDER", "gemini")).lower()
    if name == "gemini":
        return GeminiVideoProvider(
            api_key=_api_key("gemini"),
            model=os.getenv("GEMINI_TEXT_MODEL", os.getenv("GEMINI_VISION_MODEL", "gemini-video-model")),
        )
    if name == "openai":
        return OpenAIMultimodalProvider(
            api_key=_api_key("openai"),
            model=os.getenv("OPENAI_TEXT_MODEL", os.getenv("OPENAI_VISION_MODEL", "gpt-4o")),
        )
    raise ValueError(f"Unsupported AI_TEXT_PROVIDER: {name}")


@lru_cache(maxsize=3)
def get_director_text_provider(provider_name: str | None = None) -> TextAnalysisProvider:
    """Provider for strict JSON screenplay generation, deliberately separate from vision routing."""
    if settings.use_mock_ai or (provider_name or "").lower() == "mock":
        return MockMultimodalProvider(delay_seconds=settings.mock_ai_delay_seconds)
    name = (provider_name or os.getenv("AI_DIRECTOR_PROVIDER", "openai")).lower()
    if name == "openai":
        return OpenAIMultimodalProvider(
            api_key=_api_key("openai"), model=os.getenv("OPENAI_DIRECTOR_MODEL", os.getenv("OPENAI_TEXT_MODEL", "gpt-4o")),
        )
    if name == "gemini":
        return GeminiVideoProvider(
            api_key=_api_key("gemini"), model=os.getenv("GEMINI_TEXT_MODEL", "gemini-video-model"),
        )
    raise ValueError(f"Unsupported AI_DIRECTOR_PROVIDER: {name}")


@lru_cache(maxsize=2)
def get_embedding_provider(provider_name: str | None = None) -> EmbeddingProvider:
    if settings.use_mock_ai or (provider_name or "").lower() == "mock":
        return MockEmbeddingProvider()
    name = (provider_name or os.getenv("AI_EMBEDDING_PROVIDER", "open_clip")).lower()
    if name in {"open_clip", "clip", "openai_clip"}:
        return OpenCLIPEmbeddingProvider()
    raise ValueError(f"Unsupported AI_EMBEDDING_PROVIDER: {name}")


@lru_cache(maxsize=3)
def get_editing_agent_provider(provider_name: str | None = None) -> EditingAgentProvider:
    if settings.use_mock_ai or (provider_name or "").lower() == "mock":
        return MockEditingAgentProvider(delay_seconds=settings.mock_ai_delay_seconds)
    name = (provider_name or os.getenv("AI_AGENT_PROVIDER", "openai")).lower()
    if name == "openai":
        return OpenAILangChainEditingAgentProvider(
            api_key=_api_key("openai"), model=os.getenv("OPENAI_AGENT_MODEL", "gpt-4o"),
        )
    raise ValueError(f"Unsupported AI_AGENT_PROVIDER: {name}")


@lru_cache(maxsize=2)
def get_voice_clone_provider(provider_name: str | None = None) -> VoiceCloneProvider:
    if settings.use_mock_ai or (provider_name or "").lower() == "mock":
        return MockVoiceCloneProvider()
    name = (provider_name or os.getenv("AI_VOICE_PROVIDER", "xtts_v2")).lower()
    if name in {"xtts", "xtts_v2"}:
        return XTTSVoiceProvider()
    raise ValueError(f"Unsupported AI_VOICE_PROVIDER: {name}")


@lru_cache(maxsize=2)
def get_narration_tts_provider(provider_name: str | None = None) -> NarrationTTSProvider:
    if settings.use_mock_ai or (provider_name or "").lower() == "mock":
        return MockNarrationTTSProvider()
    name = (provider_name or os.getenv("AI_NARRATION_TTS_PROVIDER", "openai")).lower()
    if name == "openai":
        return OpenAINarrationTTSProvider(api_key=_api_key("openai"), model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"))
    raise ValueError(f"Unsupported AI_NARRATION_TTS_PROVIDER: {name}")


@lru_cache(maxsize=3)
def get_music_generation_provider(provider_name: str | None = None) -> MusicGenerationProvider:
    if settings.use_mock_ai or (provider_name or "").lower() == "mock":
        return MockMusicGenerationProvider()
    name = (provider_name or os.getenv("MUSIC_GENERATION_PROVIDER", "suno")).lower()
    if name not in {"suno", "udio"}:
        raise ValueError(f"Unsupported MUSIC_GENERATION_PROVIDER: {name}")
    return GatewayMusicGenerationProvider(provider=name, api_key=os.getenv("MUSIC_GENERATION_API_KEY"), base_url=os.getenv("MUSIC_GENERATION_BASE_URL"), timeout_seconds=settings.music_generation_timeout_seconds, poll_seconds=settings.music_generation_poll_seconds)
