"""Director Agent main loop: script → RAG → narration → non-destructive Timeline."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.autodirector.agents import ResearcherAgent, ScripterAgent, TTSNarrationTool
from app.autodirector.contracts import BeatResearchResult, DocumentaryScript, NarrationArtifact
from app.models.entities import (
    AutoDirectorRun, AutoDirectorStatus, Clip, MediaAsset, MediaStatus, MediaType, Timeline, TrackType,
)
from app.services.agent_timeline_versions import materialise_confirmed_timeline


class InsufficientFootageError(RuntimeError):
    pass


Progress = Callable[[int, str, str], None]


class DirectorAgent:
    """Orchestrator; child agents never mutate unrelated projects or existing Timelines."""

    def __init__(self) -> None:
        self.scripter = ScripterAgent()
        self.researcher = ResearcherAgent()
        self.tts = TTSNarrationTool()

    def run(self, db: Session, *, run: AutoDirectorRun, progress: Progress) -> Timeline:
        if run.result_timeline_id is not None and run.status == AutoDirectorStatus.READY_FOR_REVIEW:
            existing = db.get(Timeline, run.result_timeline_id)
            if existing is not None:
                return existing

        # 1. Scripter Agent: produces only a validated editorial plan, never media ids.
        if run.script_json:
            script = DocumentaryScript.model_validate(run.script_json)
        else:
            run.status = AutoDirectorStatus.SCRIPTING
            progress(12, "director_scripting", "Director 正在建立紀錄片敘事結構")
            script, provider_name = self.scripter.create_script(topic=run.topic, creative_brief=run.creative_brief_json or {})
            run.provider_name, run.script_json = provider_name, script.model_dump(mode="json")
            db.commit()

        # 2. Researcher Agent: pgvector search spans every project owned by this user.
        existing_beats = (run.research_json or {}).get("beats", [])
        if existing_beats:
            research = [BeatResearchResult.model_validate(item) for item in existing_beats]
        else:
            run.status = AutoDirectorStatus.RESEARCHING
            progress(38, "director_research", "Researcher 正在跨專案檢索語意素材")
            research = self.researcher.retrieve_for_script(db, owner_id=run.requested_by_id, script=script)
            run.research_json = {"beats": [item.model_dump(mode="json") for item in research]}
            db.commit()
        missing = [item.beat_id for item in research if not item.candidates]
        if missing:
            raise InsufficientFootageError(f"No grounded media was found for screenplay beats: {', '.join(missing)}")

        # 3. Narration tool: generated once, kept as a MinIO artifact for render/review.
        language = str((run.creative_brief_json or {}).get("language", "zh-TW"))
        narration_text = "\n\n".join(beat.narration for beat in script.beats)
        if run.narration_key:
            narration = NarrationArtifact(
                storage_key=run.narration_key, duration_seconds=script.total_duration_seconds,
                language=language, text=narration_text,
            )
        else:
            run.status = AutoDirectorStatus.NARRATING
            progress(62, "director_narration", "正在生成旁白音軌")
            narration = self.tts.synthesize(
                project_id=run.project_id, run_id=run.id, text=narration_text,
                language=language, duration_seconds=script.total_duration_seconds,
            )
            run.narration_key = narration.storage_key
            db.commit()

        # 4. Editor Agent: only grounds clips in retrieved ids/timestamps, then writes a new review-only Timeline.
        run.status = AutoDirectorStatus.EDITING
        progress(82, "director_editing", "Editor 正在生成可審閱的多軌時間軸")
        timeline = self._build_timeline(db, run=run, script=script, research=research, narration=narration)
        run.result_timeline_id = timeline.id
        run.status = AutoDirectorStatus.READY_FOR_REVIEW
        db.commit()
        progress(100, "director_ready_for_review", "您的紀錄片已準備好審閱")
        return timeline

    @staticmethod
    def _build_timeline(
        db: Session,
        *,
        run: AutoDirectorRun,
        script: DocumentaryScript,
        research: list[BeatResearchResult],
        narration: NarrationArtifact,
    ) -> Timeline:
        max_version = db.scalar(select(func.max(Timeline.version)).where(Timeline.project_id == run.project_id)) or 0
        timeline = Timeline(
            project_id=run.project_id,
            name=f"Auto Director: {script.title}"[:200],
            version=int(max_version) + 1,
            # Review-only prevents an autonomous run from replacing the editor's active version.
            is_current=False,
            settings_json={
                "auto_director": {
                    "run_id": str(run.id),
                    "title": script.title,
                    "summary": script.summary,
                    "script": script.model_dump(mode="json"),
                    "narration": narration.model_dump(mode="json"),
                    "review_required": True,
                },
                "clip_layout": {},
            },
        )
        db.add(timeline)
        db.flush()
        by_beat = {item.beat_id: item for item in research}
        cursor = 0.0
        layout: dict[str, dict[str, float]] = {}
        citations: list[dict[str, Any]] = []
        for order, beat in enumerate(script.beats):
            selected = by_beat[beat.id].candidates[0]
            source_start = selected.source_start
            source_end = selected.source_end
            # Keyframe embeddings have a point timestamp; expand them into a safe source window.
            if source_end <= source_start:
                available_end = selected.duration_seconds or (source_start + beat.target_duration_seconds)
                source_end = min(available_end, source_start + beat.target_duration_seconds)
            elif selected.duration_seconds and (source_end - source_start) < beat.target_duration_seconds:
                # Transcript embeddings are often sentence-sized. Expand around the semantic hit
                # without exceeding the source asset so narration and picture pacing stay aligned.
                midpoint = (source_start + source_end) / 2
                source_start = max(0.0, midpoint - beat.target_duration_seconds / 2)
                source_end = min(float(selected.duration_seconds), source_start + beat.target_duration_seconds)
                source_start = max(0.0, source_end - beat.target_duration_seconds)
            if source_end <= source_start:
                raise InsufficientFootageError(f"Retrieved segment for {beat.id} has no usable duration")
            clip = Clip(
                timeline_id=timeline.id,
                source_asset_id=selected.media_asset_id,
                source_start=source_start,
                source_end=source_end,
                track=TrackType.MAIN_VIDEO,
                z_index=0,
                # Narration owns the editorial audio; source audio is intentionally muted.
                audio_enabled=False,
                audio_effects=[],
                order_index=order,
                enabled=True,
            )
            db.add(clip)
            db.flush()
            layout[str(clip.id)] = {"timeline_start": cursor}
            cursor += source_end - source_start
            citations.append({
                "beat_id": beat.id,
                "query": by_beat[beat.id].query,
                "media_asset_id": str(selected.media_asset_id),
                "source_project_id": str(selected.project_id),
                "source_start": source_start,
                "source_end": source_end,
                "similarity_score": selected.similarity_score,
            })
        settings = dict(timeline.settings_json)
        narration_asset = db.scalar(select(MediaAsset).where(MediaAsset.storage_key == narration.storage_key))
        if narration_asset is None:
            narration_asset = MediaAsset(
                project_id=run.project_id,
                filename=f"auto-director-{run.id}-narration.wav",
                storage_key=narration.storage_key,
                media_type=MediaType.AUDIO,
                status=MediaStatus.READY,
                mime_type="audio/wav",
                duration_seconds=narration.duration_seconds,
                audio_key=narration.storage_key,
                metadata_json={"source": "auto_director_tts", "run_id": str(run.id), "language": narration.language},
            )
            db.add(narration_asset)
            db.flush()
        narration_clip = Clip(
            timeline_id=timeline.id,
            source_asset_id=narration_asset.id,
            source_start=0.0,
            source_end=narration.duration_seconds,
            track=TrackType.AUDIO_OVERLAY,
            z_index=0,
            audio_enabled=True,
            audio_effects=[],
            order_index=len(script.beats),
            enabled=True,
        )
        db.add(narration_clip)
        db.flush()
        layout[str(narration_clip.id)] = {"timeline_start": 0.0}
        settings["clip_layout"] = layout
        settings["auto_director"]["narration"]["media_asset_id"] = str(narration_asset.id)
        settings["auto_director"]["research_citations"] = citations
        settings["auto_director"]["visual_duration_seconds"] = cursor
        timeline.settings_json = settings
        materialise_confirmed_timeline(timeline)
        db.flush()
        return timeline
