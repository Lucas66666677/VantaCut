from __future__ import annotations

import copy
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from app.ai.lecturas_prompts import LECTURAS_SYSTEM_PROMPT, lecturas_response_schema, lecturas_user_prompt
from app.ai.providers.factory import get_vision_provider
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AvatarProfile, MediaAsset, MediaStatus, MediaType, Timeline
from app.services.agent_timeline_versions import clone_timeline_version
from app.services.audio_description import synthesize_description
from app.services.avatar_animation import get_audio2face_provider, write_animation_document
from app.services.avatar_renderer import render_avatar_rgba
from app.services.lecturas import LecturasError, idle_assistant_motion, mux_avatar_with_voice, transcript_for_planning, validate_plan, wav_duration
from app.services.storage import download_object, upload_object
from app.worker import celery_app


def _output_duration(transcript: list[dict[str, Any]]) -> float:
    return max(float(item["end_time"]) for item in transcript)


def _set_run(timeline: Timeline, run_id: str, patch: dict[str, Any]) -> None:
    settings = copy.deepcopy(dict(timeline.settings_json or {})); runs = list(settings.get("lecturas_runs", []))
    settings["lecturas_runs"] = [{**item, **patch} if item.get("run_id") == run_id else item for item in runs]
    timeline.settings_json = settings


@celery_app.task(bind=True, name="lecturas.generate_interventions")
def generate_lecturas_interventions(self, source_timeline_id: str, run_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); source: Timeline | None = None
    try:
        source = db.get(Timeline, UUID(source_timeline_id))
        asset = db.get(MediaAsset, UUID(str(request["source_asset_id"])))
        profile = db.get(AvatarProfile, UUID(str(request["avatar_profile_id"])))
        if source is None or asset is None or profile is None or asset.project_id != source.project_id or profile.owner_id != source.project.owner_id:
            raise LecturasError("Timeline, source asset, or licensed assistant avatar is invalid")
        transcript = transcript_for_planning(dict(source.settings_json or {}))
        _set_run(source, run_id, {"status": "planning"}); db.commit()
        source_key = asset.proxy_key if request.get("use_proxy", True) and asset.proxy_key else asset.storage_key
        publish_project_status(str(source.project_id), progress=12, stage="lecturas_understanding", message="Lecturas 正在理解逐字稿與畫面脈絡", job_id=run_id)
        with tempfile.TemporaryDirectory(prefix=f"lecturas-{run_id}-") as temporary:
            workdir = Path(temporary); source_video, bundle = workdir / "source.mp4", workdir / "assistant.bundle"
            download_object(source_key, str(source_video)); download_object(profile.asset_bundle_key, str(bundle))
            raw_plan = get_vision_provider().analyze_video(
                str(source_video), f"{LECTURAS_SYSTEM_PROMPT}\n\n{lecturas_user_prompt(transcript=transcript, assistant_name=str(request['assistant_name']), max_interventions=int(request['max_interventions']))}", response_schema=lecturas_response_schema(),
                context={"task": "lecturas_interventions", "transcript": transcript, "assistant_name": request["assistant_name"], "sampled_video_path": str(source_video)},
            )
            plan = validate_plan(raw_plan, max_interventions=int(request["max_interventions"]), output_duration=_output_duration(transcript))
            publish_project_status(str(source.project_id), progress=35, stage="lecturas_scripting", message=f"Lecturas 已提出 {len(plan)} 個可審閱插播腳本", job_id=run_id)
            rendered: list[dict[str, Any]] = []
            for index, intervention in enumerate(plan, start=1):
                narration, animation, alpha, movie = workdir / f"narration-{index}.wav", workdir / f"animation-{index}.json", workdir / f"assistant-{index}-alpha.mov", workdir / f"assistant-{index}.mov"
                synthesize_description(text=str(intervention["script"]), language=str(request["language"]), output_wav=narration)
                duration = min(20.0, max(.5, wav_duration(narration)))
                blendshapes = get_audio2face_provider().generate_blendshapes(narration)
                write_animation_document(animation, blendshapes=blendshapes, motion=idle_assistant_motion(duration), rig_mapping=dict(profile.rig_mapping_json or {}))
                publish_project_status(str(source.project_id), progress=35 + int(index / max(1, len(plan)) * 45), stage="lecturas_avatar_render", message=f"正在渲染 Lecturas 插播 {index}/{len(plan)}", job_id=run_id)
                render_avatar_rgba(animation_path=animation, avatar_bundle_path=bundle, output_path=alpha, width=int(asset.width or 1280), height=int(asset.height or 720))
                mux_avatar_with_voice(alpha, narration, movie)
                key = f"projects/{source.project_id}/lecturas/{run_id}/assistant-{index}.mov"; upload_object(key, str(movie), "video/quicktime")
                generated = MediaAsset(project_id=source.project_id, filename=f"lecturas-{index}.mov", storage_key=key, media_type=MediaType.VIDEO, status=MediaStatus.READY, mime_type="video/quicktime", width=asset.width, height=asset.height, duration_seconds=duration, metadata_json={"lecturas_generated": True, "alpha": True, "audio_enabled": True, "provenance": "digital_avatar", "disclosure": "AI teaching assistant"})
                db.add(generated); db.flush()
                rendered.append({**intervention, "asset_id": str(generated.id), "asset_key": key, "duration_seconds": round(duration, 3), "assistant_name": request["assistant_name"], "disclosure": "AI teaching assistant / digital avatar"})
        target = clone_timeline_version(db, source, label=f"Lecturas review: {source.name}")
        target.settings_json = {**dict(target.settings_json or {}), "lecturas": {"status": "completed", "run_id": run_id, "assistant_name": request["assistant_name"], "review_required": True, "disclosure": "AI teaching assistant / digital avatar", "interventions": rendered}}
        _set_run(source, run_id, {"status": "completed", "result_timeline_id": str(target.id), "intervention_count": len(rendered)})
        db.commit()
        publish_project_status(str(source.project_id), progress=100, stage="lecturas_completed", status="completed", message="Lecturas 已建立可審閱的雙主持時間軸版本", job_id=run_id)
        return {"run_id": run_id, "result_timeline_id": str(target.id), "interventions": len(rendered)}
    except Exception as exc:
        db.rollback()
        if source is not None:
            current = db.get(Timeline, source.id)
            if current is not None:
                _set_run(current, run_id, {"status": "failed", "error": str(exc)}); db.commit()
            publish_project_status(str(source.project_id), progress=0, stage="lecturas_failed", status="failed", message="Lecturas 插播生成失敗", job_id=run_id)
        raise
    finally:
        db.close()
