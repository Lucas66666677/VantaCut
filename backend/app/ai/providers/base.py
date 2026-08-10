from abc import ABC, abstractmethod
from typing import Any

from app.ai.providers.schemas import Transcript


class AIProvider(ABC):
    """Common provider contract shared by all AI integrations."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier used in logs and persistence."""


class MultimodalProvider(AIProvider):
    """Interface for video/image understanding providers."""

    @abstractmethod
    def analyze_video(
        self,
        video_uri: str,
        prompt: str,
        *,
        response_schema: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Analyze video/frames with optional structured context and return JSON data."""


class ASRProvider(AIProvider):
    """Interface for speech-to-text providers with timestamp alignment."""

    @abstractmethod
    def transcribe(
        self,
        audio_uri: str,
        *,
        language: str | None = None,
        word_timestamps: bool = True,
    ) -> Transcript:
        """Transcribe audio and return segment and word-level timestamps."""


class VoiceCloneProvider(AIProvider):
    """Creates a reusable speaker-conditioning artifact and synthesizes consented replacements."""

    @abstractmethod
    def extract_profile(self, reference_wav: str, artifact_path: str) -> dict[str, Any]:
        """Write an opaque conditioning artifact; never return raw embeddings to an API caller."""

    @abstractmethod
    def synthesize(
        self,
        *,
        text: str,
        language: str,
        artifact_path: str,
        output_wav: str,
        emotion: str = "neutral",
        tempo: float = 1.0,
    ) -> dict[str, Any]:
        """Generate WAV audio from a stored profile and return non-sensitive synthesis metadata."""


class NarrationTTSProvider(AIProvider):
    """Creates non-cloned narration from an approved built-in provider voice."""

    @abstractmethod
    def synthesize_narration(
        self, *, text: str, voice: str, instructions: str, speed: float, output_wav: str,
    ) -> dict[str, Any]:
        """Write a WAV file and return safe provider metadata."""


class MusicGenerationProvider(AIProvider):
    """Creates an original, project-scoped music file through a licensed provider gateway."""

    @abstractmethod
    def generate_music(
        self, *, prompt: str, duration_seconds: float, instrumental: bool, output_path: str,
    ) -> dict[str, Any]:
        """Write generated audio locally and return provider-safe metadata such as detected vocals."""


class TextAnalysisProvider(AIProvider):
    """Interface for LLM tasks that use text-only structured output."""

    @abstractmethod
    def extract_education_keywords(
        self,
        transcript_text: str,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return JSON-compatible educational keyword candidates."""

    @abstractmethod
    def generate_structured_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate validated JSON for non-video planning tasks such as scripting."""


class EmbeddingProvider(AIProvider):
    """Shared image/text embedding space for semantic media retrieval."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Fixed vector dimension persisted in pgvector."""

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Encode a natural-language search query or transcript segment."""

    @abstractmethod
    def embed_image(self, image_path: str) -> list[float]:
        """Encode one extracted video keyframe."""


class EditingAgentProvider(AIProvider):
    """Plans constrained Timeline tool calls from a system/user prompt pair."""

    @abstractmethod
    def plan_edit(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: list[Any],
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return provider-normalised tool calls plus an optional clarification message."""
