"""Asynchronous Auto-Narrative: visual notes -> script -> TTS -> renderable vlog timeline."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.ai.providers.factory import get_narration_tts_provider, get_text_provider, get_vision_provider
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import Clip, MediaAsset, MediaStatus, Project, RenderJob, Timeline, TrackType, User
from app.services.auto_narrative import extract_sampled_frames, plan_narrative, select_source_window, understand_asset
from app.services.entitlements import validate_render_entitlement
from app.services.narration_tts import NARRATION_STYLES, narration_cues, wav_duration_seconds
from app.services.storage import download_object, upload_bytes, upload_object
from app.services.subtitles import cues_to_ass, cues_to_srt
from app.tasks.render_tasks import render_final_timeline
from app.worker import celery_app


def _narration_style(tone: str) -> str:
    return "funny_host" if tone == "funny_vlogger" else "warm_friend"


@celery_app.task(bind=True, name="auto_narrative.generate")
def generate_auto_narrative(self, project_id: str, request: dict[str, Any]) -> dict[str, str]:
    db = SessionLocal(); project: Project | None = None
    try:
        project = db.get(Project, UUID(project_id)); user = db.get(User, UUID(str(request["user_id"])))
        if project is None or user is None or project.owner_id != user.id:
            raise ValueError("Project ownership changed before Auto-Narrative generation")
        requested_ids = [UUID(str(item)) for item in request["media_asset_ids"]]
        found = db.scalars(select(MediaAsset).where(MediaAsset.id.in_(requested_ids), MediaAsset.project_id == project.id)).all()
        by_id = {str(asset.id): asset for asset in found}
        assets = [by_id[str(asset_id)] for asset_id in requested_ids if str(asset_id) in by_id]
        if len(assets) != len(requested_ids) or any(asset.status != MediaStatus.READY for asset in assets):
            raise ValueError("Every selected source video must be ready")
        if any(float(asset.duration_seconds or 0) < .5 for asset in assets):
            raise ValueError("Every selected source video needs at least 0.5 seconds of media")

        publish_project_status(project_id, progress=5, stage="auto_narrative_preparing", message="正在準備素材與 AI 導演", job_id=self.request.id)
        visual_notes = []
        with tempfile.TemporaryDirectory(prefix=f"auto-narrative-{project_id}-") as temporary:
            workdir = Path(temporary); vision = get_vision_provider(); text = get_text_provider()
            for index, asset in enumerate(assets):
                local_proxy = workdir / f"source-{index}.mp4"
                download_object(asset.proxy_key or asset.storage_key, str(local_proxy))
                frames = extract_sampled_frames(local_proxy, workdir / f"frames-{index}", duration=float(asset.duration_seconds or 0), count=4)
                visual_notes.append(understand_asset(vision, asset=asset, local_proxy=local_proxy, frame_paths=frames))
                publish_project_status(project_id, progress=8 + int((index + 1) / len(assets) * 37), stage="auto_narrative_understanding", message=f"AI 正在理解素材 {index + 1}/{len(assets)}", job_id=self.request.id)

            plan = plan_narrative(
                text, understandings=visual_notes, tone=str(request.get("tone", "funny_vlogger")),
                language=str(request.get("language", "zh")), target_duration_seconds=int(request.get("target_duration_seconds", 30)),
            )
            publish_project_status(project_id, progress=50, stage="auto_narrative_scripting", message="AI 正在撰寫旁白與分鏡", job_id=self.request.id)
            style_id = _narration_style(str(request.get("tone", "funny_vlogger"))); style = NARRATION_STYLES[style_id]
            tts = get_narration_tts_provider(); synthesized: list[dict[str, Any]] = []
            output_cursor = 0.0
            for index, beat in enumerate(plan.beats):
                asset = by_id[beat.asset_id]
                wav_path = workdir / f"narration-{index:02d}.wav"
                tts.synthesize_narration(text=beat.narration, voice=style["voice"], instructions=style["instructions"], speed=1.0, output_wav=str(wav_path))
                narration_duration = wav_duration_seconds(wav_path)
                source_start, source_end = select_source_window(asset, beat.source_start, beat.source_end, narration_duration)
                cues = narration_cues(beat.narration, start_time=output_cursor, duration=narration_duration, id_prefix=f"auto-{index + 1}")
                synthesized.append({
                    "id": str(uuid4()), "beat": beat, "asset": asset, "wav_path": wav_path, "duration": narration_duration,
                    "source_start": source_start, "source_end": source_end, "timeline_start": output_cursor, "cues": cues,
                })
                output_cursor += narration_duration
                publish_project_status(project_id, progress=53 + int((index + 1) / len(plan.beats) * 25), stage="auto_narrative_tts", message=f"正在生成旁白 {index + 1}/{len(plan.beats)}", job_id=self.request.id)

            validate_render_entitlement(user.subscription_tier, output_cursor, str(request.get("resolution", "1080p")))
            if bool(request.get("auto_render", True)) and user.subscription_tier.value == "free" and user.render_credits <= 0:
                raise ValueError("免費渲染點數已用完")
            db.query(Timeline).filter(Timeline.project_id == project.id, Timeline.is_current.is_(True)).update({Timeline.is_current: False}, synchronize_session=False)
            timeline = Timeline(project_id=project.id, name=f"AI 旁白 Vlog・{plan.title}", version=int(db.query(Timeline).filter(Timeline.project_id == project.id).count()) + 1, is_current=True)
            db.add(timeline); db.flush()

            clips: list[dict[str, Any]] = []; narration_entries: list[dict[str, Any]] = []; all_cues = []
            for index, item in enumerate(synthesized):
                audio_key = f"projects/{project.id}/timelines/{timeline.id}/auto-narrative/{item['id']}.wav"
                upload_object(audio_key, str(item["wav_path"]), "audio/wav")
                clip_id = str(uuid4()); beat = item["beat"]
                clip = {
                    "id": clip_id, "source_asset_id": str(item["asset"].id), "source_start": item["source_start"], "source_end": item["source_end"],
                    "timeline_start": round(item["timeline_start"], 3), "action": "keep", "audio_enabled": False, "gain_db": -80,
                    "confidence_score": round(visual_notes[index % len(visual_notes)].confidence * 100, 1), "reason": f"Auto-Narrative: {beat.visual_role} · {beat.narration}",
                }
                clips.append(clip)
                db.add(Clip(id=UUID(clip_id), timeline_id=timeline.id, source_asset_id=item["asset"].id, source_start=item["source_start"], source_end=item["source_end"], track=TrackType.MAIN_VIDEO, z_index=0, audio_enabled=False, order_index=index))
                narration_entries.append({"id": item["id"], "status": "completed", "text": beat.narration, "style": style_id, "timeline_start": round(item["timeline_start"], 3), "duration": round(item["duration"], 3), "audio_key": audio_key, "cues": [cue.model_dump(mode="json") for cue in item["cues"]]})
                all_cues.extend(item["cues"])

            subtitle_base = f"projects/{project.id}/timelines/{timeline.id}/auto-narrative"
            srt_key, ass_key = f"{subtitle_base}/subtitles.srt", f"{subtitle_base}/subtitles.ass"
            upload_bytes(srt_key, cues_to_srt(all_cues).encode("utf-8"), "application/x-subrip")
            upload_bytes(ass_key, cues_to_ass(all_cues, preset="viral_yellow", aspect_ratio=str(request.get("aspect_ratio", "9:16"))).encode("utf-8"), "text/x-ssa")
            document = {
                "schema": "com.aivideo.auto-narrative.v1", "source_asset_id": clips[0]["source_asset_id"],
                "tracks": [
                    {"id": "main-video", "type": "main_video", "z_index": 0, "clips": clips},
                    {"id": "auto-narration-audio", "type": "audio_overlay", "z_index": 30, "clips": [{"id": item["id"], "kind": "tts_narration", "audio_key": item["audio_key"], "timeline_start": item["timeline_start"], "source_start": 0, "source_end": item["duration"], "action": "keep", "audio_enabled": True} for item in narration_entries]},
                ],
                "auto_narrative": {"title": plan.title, "summary": plan.summary, "script": plan.script, "tone": request.get("tone"), "visual_notes": [note.model_dump(mode="json") for note in visual_notes]},
            }
            bgm_asset_id = request.get("bgm_asset_id")
            timeline.settings_json = {
                "confirmed_timeline": document, "tts_narrations": narration_entries,
                "subtitles": {"srt_key": srt_key, "ass_key": ass_key},
                "auto_narrative": {"status": "assembled", "task_id": self.request.id, "tone": request.get("tone"), "bgm_asset_id": str(bgm_asset_id) if bgm_asset_id else None, "bgm_mix_level": .12, "bgm_style": "lofi", "title": plan.title},
            }
            render_job_id = ""
            if bool(request.get("auto_render", True)):
                if user.subscription_tier.value == "free": user.render_credits -= 1
                render_job = RenderJob(project_id=project.id, timeline_id=timeline.id); db.add(render_job); db.flush(); render_job_id = str(render_job.id)
            db.commit()

        publish_project_status(project_id, progress=84, stage="auto_narrative_timeline_ready", message="旁白、分鏡與字幕已自動組裝", job_id=render_job_id or self.request.id, extra={"timeline_id": str(timeline.id), "render_job_id": render_job_id})
        if render_job_id:
            render_final_timeline.delay(render_job_id, str(request.get("resolution", "1080p")), str(request.get("aspect_ratio", "9:16")))
        else:
            publish_project_status(project_id, progress=100, stage="auto_narrative_completed", status="completed", message="AI 旁白 Vlog 已完成，等待手動導出", job_id=self.request.id, extra={"timeline_id": str(timeline.id)})
        return {"timeline_id": str(timeline.id), "render_job_id": render_job_id, "status": "queued" if render_job_id else "completed"}
    except Exception as exc:
        db.rollback()
        if project is not None:
            publish_project_status(project_id, progress=0, stage="auto_narrative_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
