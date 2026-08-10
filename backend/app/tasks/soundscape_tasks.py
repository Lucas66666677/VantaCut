"""Generate an editable, spatial soundscape bed for a confirmed timeline."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.ai_retry import is_retryable_ai_error, retry_ai_task
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, Timeline
from app.services.bgm_recommender import extract_bgm_frames, kept_segments_from_timeline, uniformly_sample_kept_timeline
from app.services.soundscape import get_foley_provider, plan_soundscape
from app.services.spatial_audio import SpatialSource, render_spatial_mix
from app.services.storage import download_object, upload_object
from app.worker import celery_app


def _normalise_event_audio(source: Path, destination: Path) -> None:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(source), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(destination)],
            check=True, capture_output=True, text=True, timeout=20 * 60,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Generated foley normalisation timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError((exc.stderr or "Generated foley normalisation failed")[-2000:]) from exc


@celery_app.task(bind=True, name="soundscape.generate_for_timeline")
def generate_soundscape_for_timeline(self, timeline_id: str, layout: str = "5.1") -> dict[str, Any]:
    db = SessionLocal()
    timeline: Timeline | None = None
    try:
        if layout not in {"5.1", "7.1.4"}:
            raise ValueError("Soundscape layout must be 5.1 or 7.1.4")
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise ValueError("Timeline not found")
        confirmed = dict((timeline.settings_json or {}).get("confirmed_timeline", {}))
        asset_id = confirmed.get("source_asset_id")
        if not asset_id:
            raise ValueError("Confirmed timeline has no source asset")
        asset = db.get(MediaAsset, UUID(asset_id))
        if asset is None or asset.project_id != timeline.project_id:
            raise ValueError("Confirmed source asset is invalid")
        segments = kept_segments_from_timeline(list(confirmed.get("segments", [])))
        output_duration = sum(segment.source_end - segment.source_start for segment in segments)
        samples = uniformly_sample_kept_timeline(segments)
        publish_project_status(str(timeline.project_id), progress=10, stage="soundscape_preparing", message="正在準備畫面與節奏脈絡", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"soundscape-{timeline.id}-") as temporary:
            workdir = Path(temporary)
            source = workdir / "source.mp4"
            download_object(asset.proxy_key or asset.storage_key, str(source))
            publish_project_status(str(timeline.project_id), progress=25, stage="soundscape_visual_analysis", message="正在分析畫面中的環境與動作", job_id=self.request.id)
            frames = extract_bgm_frames(source, samples, workdir / "frames")
            plan = plan_soundscape(asset.proxy_key or asset.storage_key, frames, output_duration=output_duration)
            provider = get_foley_provider()
            rendered_sources: list[SpatialSource] = []
            for index, event in enumerate(plan.events):
                publish_project_status(str(timeline.project_id), progress=35 + int((index / max(1, len(plan.events))) * 45), stage="soundscape_generating", message=f"正在生成 {event.kind} 音效", job_id=self.request.id)
                raw, normalized = workdir / f"event-{index:02d}-raw.wav", workdir / f"event-{index:02d}.wav"
                provider.generate(event, raw)
                _normalise_event_audio(raw, normalized)
                rendered_sources.append(SpatialSource(
                    path=normalized, start_time=event.start_time, gain_db=event.gain_db,
                    x=event.position.x, y=event.position.y, z=event.position.z,
                ))
            spatial_mix = workdir / f"soundscape-{layout.replace('.', '_')}.wav"
            publish_project_status(str(timeline.project_id), progress=83, stage="soundscape_spatial_mix", message="正在渲染沉浸式空間音訊", job_id=self.request.id)
            render_spatial_mix(rendered_sources, spatial_mix, duration_seconds=output_duration, layout=layout)  # type: ignore[arg-type]
            key = f"projects/{timeline.project_id}/derived/timelines/{timeline.id}/soundscape-{layout.replace('.', '_')}.wav"
            upload_object(key, str(spatial_mix), "audio/wav")

        timeline.settings_json = {
            **dict(timeline.settings_json or {}),
            "soundscape": {
                "status": "completed", "layout": layout, "provider": provider.name,
                "spatial_mix_key": key, "events": [event.model_dump(mode="json") for event in plan.events],
                "sample_rate": 48_000, "duration_seconds": round(output_duration, 3),
            },
        }
        db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="soundscape_completed", status="completed", message="電影級環境音預覽完成", job_id=self.request.id)
        return {"timeline_id": timeline_id, "layout": layout, "soundscape_key": key, "event_count": len(plan.events)}
    except Exception as exc:
        db.rollback()
        if timeline is not None and is_retryable_ai_error(exc):
            retry_ai_task(self, exc, project_id=str(timeline.project_id), stage="soundscape_visual_analysis", message="多模態畫面分析暫時不可用", job_id=self.request.id)
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "soundscape": {"status": "failed", "error": str(exc)}}
                db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="soundscape_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
