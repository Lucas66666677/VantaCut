from __future__ import annotations

import math
import shlex
import subprocess
import tempfile
import wave
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers.factory import get_director_text_provider, get_embedding_provider
from app.autodirector.contracts import (
    BeatResearchResult, DocumentaryScript, NarrationArtifact, RetrievedMediaSegment,
)
from app.autodirector.prompts import SCRIPTER_SYSTEM_PROMPT, script_schema, scripter_user_prompt
from app.core.config import settings
from app.models.entities import MediaAsset, MediaEmbeddingSegment, MediaStatus, Project
from app.services.storage import upload_object


class ScripterAgent:
    def create_script(self, *, topic: str, creative_brief: dict[str, object]) -> tuple[DocumentaryScript, str]:
        provider = get_director_text_provider()
        document = provider.generate_structured_json(
            system_prompt=SCRIPTER_SYSTEM_PROMPT,
            user_prompt=scripter_user_prompt(topic=topic, brief=creative_brief),
            response_schema=script_schema(),
        )
        return DocumentaryScript.model_validate(document), provider.name


class ResearcherAgent:
    """Cross-project RAG restricted to media owned by the requesting user."""

    def retrieve_for_script(
        self, db: Session, *, owner_id: UUID, script: DocumentaryScript, per_beat: int = 8,
    ) -> list[BeatResearchResult]:
        provider = get_embedding_provider()
        results: list[BeatResearchResult] = []
        for beat in script.beats:
            query = f"{beat.visual_query}. {beat.narration[:240]}"
            vector = provider.embed_text(query)
            distance = MediaEmbeddingSegment.embedding.cosine_distance(vector).label("distance")
            rows = db.execute(
                select(MediaEmbeddingSegment, MediaAsset, Project, distance)
                .join(MediaAsset, MediaEmbeddingSegment.media_asset_id == MediaAsset.id)
                .join(Project, MediaAsset.project_id == Project.id)
                .where(Project.owner_id == owner_id, MediaAsset.status == MediaStatus.READY)
                .order_by(distance)
                .limit(per_beat)
            ).all()
            candidates = [
                RetrievedMediaSegment(
                    media_asset_id=asset.id,
                    project_id=project.id,
                    filename=asset.filename,
                    source_start=float(segment.source_start),
                    source_end=float(segment.source_end),
                    modality=segment.modality,  # type: ignore[arg-type]
                    similarity_score=max(0.0, min(1.0, 1.0 - float(row_distance))),
                    matched_text=(segment.metadata_json or {}).get("text"),
                    duration_seconds=float(asset.duration_seconds) if asset.duration_seconds is not None else None,
                )
                for segment, asset, project, row_distance in rows
            ]
            results.append(BeatResearchResult(beat_id=beat.id, query=query, candidates=candidates))
        return results


class TTSNarrationTool:
    """TTS adapter: command-based in production and deterministic WAV in development/tests."""

    def synthesize(self, *, project_id: UUID, run_id: UUID, text: str, language: str, duration_seconds: float) -> NarrationArtifact:
        with tempfile.TemporaryDirectory(prefix=f"director-tts-{run_id}-") as directory:
            workdir = Path(directory)
            output_path = workdir / "narration.wav"
            if settings.tts_command:
                text_path = workdir / "narration.txt"
                text_path.write_text(text, encoding="utf-8")
                command = [
                    part.format(text_file=str(text_path), output=str(output_path), language=language)
                    for part in shlex.split(settings.tts_command)
                ]
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=900)
                if not output_path.exists():
                    raise RuntimeError("TTS_COMMAND finished without creating the requested output file")
            elif settings.use_mock_ai:
                self._write_silent_wav(output_path, duration_seconds)
            else:
                raise RuntimeError("TTS_COMMAND is required for autonomous narration outside development mode")
            key = f"projects/{project_id}/auto-director/{run_id}/narration.wav"
            upload_object(key, str(output_path), "audio/wav")
        return NarrationArtifact(storage_key=key, duration_seconds=duration_seconds, language=language, text=text)

    @staticmethod
    def _write_silent_wav(path: Path, duration_seconds: float) -> None:
        sample_rate, channels, width = 16_000, 1, 2
        frames = int(math.ceil(duration_seconds * sample_rate))
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(width)
            wav.setframerate(sample_rate)
            wav.writeframes(b"\x00\x00" * frames)
