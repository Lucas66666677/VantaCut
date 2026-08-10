"""Async one-click assembly: score proxy footage, fill template slots, then enqueue FFmpeg."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import Clip, MediaAsset, MediaStatus, Project, RenderJob, Timeline, TrackType, User
from app.services.beat_sync import analyze_music
from app.services.entitlements import validate_render_entitlement
from app.services.one_click_templates import build_template_timeline, get_template
from app.services.storage import download_object
from app.tasks.render_tasks import render_final_timeline
from app.worker import celery_app


def _score_video(proxy_path: Path, asset: MediaAsset) -> dict[str, Any] | None:
    """Use inexpensive CV signals; a multimodal ranker can later replace this contract."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OpenCV and NumPy are required for one-click scoring") from exc
    capture = cv2.VideoCapture(str(proxy_path))
    if not capture.isOpened():
        return None
    fps = float(capture.get(cv2.CAP_PROP_FPS) or asset.fps or 30.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = float(asset.duration_seconds or (frame_count / fps if fps else 0))
    if duration < .35:
        capture.release(); return None
    classifier = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    samples = max(4, min(12, int(duration * 2)))
    best: tuple[float, float] | None = None; previous_gray = None
    try:
        for index in range(samples):
            timestamp = min(max(.05, duration * (index + .5) / samples), max(.05, duration - .05))
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000); ok, frame = capture.read()
            if not ok: continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharpness = min(1.0, float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 650.0)
            faces = classifier.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=4, minSize=(28, 28)) if not classifier.empty() else []
            face_score = min(1.0, len(faces) * .8)
            motion = 0.0 if previous_gray is None else min(1.0, float(np.mean(cv2.absdiff(gray, previous_gray))) / 28.0)
            score = sharpness * .58 + face_score * .28 + motion * .14
            if best is None or score > best[0]: best = (score, timestamp)
            previous_gray = gray
    finally:
        capture.release()
    if best is None: return None
    window = min(5.0, duration)
    start = min(max(0.0, best[1] - window / 2), max(0.0, duration - window))
    return {"asset_id": str(asset.id), "score": round(best[0], 4), "source_start": round(start, 3), "source_end": round(start + window, 3)}


@celery_app.task(bind=True, name="one_click.generate_video")
def generate_one_click_video(self, project_id: str, user_id: str, request: dict[str, Any]) -> dict[str, str]:
    db = SessionLocal(); project: Project | None = None
    try:
        project = db.get(Project, UUID(project_id)); user = db.scalar(select(User).where(User.id == UUID(user_id)).with_for_update())
        if project is None or user is None or project.owner_id != user.id: raise ValueError("Project ownership changed before generation")
        template = get_template(str(request["template_id"])); requested_ids = [UUID(str(item)) for item in request["media_asset_ids"]]
        assets = db.scalars(select(MediaAsset).where(MediaAsset.id.in_(requested_ids), MediaAsset.project_id == project.id)).all()
        if len(assets) != len(set(requested_ids)) or any(item.status != MediaStatus.READY for item in assets):
            raise ValueError("All one-click source videos must be ready project media")
        publish_project_status(project_id, progress=5, stage="one_click_preparing", message="正在讀取模板與素材代理檔", job_id=self.request.id)
        ranked: list[dict[str, Any]] = []; detected_beats: list[float] | None = None
        with tempfile.TemporaryDirectory(prefix=f"one-click-{project_id}-") as temporary:
            workdir = Path(temporary)
            for index, asset in enumerate(assets):
                local = workdir / f"source-{index}.mp4"; download_object(asset.proxy_key or asset.storage_key, str(local))
                candidate = _score_video(local, asset)
                if candidate: ranked.append(candidate)
                publish_project_status(project_id, progress=10 + int((index + 1) / max(1, len(assets)) * 45), stage="one_click_scoring", message=f"正在評分素材 {index + 1}/{len(assets)}", job_id=self.request.id)
            bgm_id = request.get("bgm_asset_id")
            if bgm_id:
                bgm = db.get(MediaAsset, UUID(str(bgm_id)))
                if bgm is None or bgm.project_id != project.id or bgm.status != MediaStatus.READY: raise ValueError("Selected BGM is not ready")
                bgm_path = workdir / "selected-bgm"; download_object(bgm.audio_key or bgm.storage_key, str(bgm_path))
                detected_beats = analyze_music(bgm_path).beats
        ranked.sort(key=lambda item: float(item["score"]), reverse=True)
        document, transitions = build_template_timeline(template=template, ranked_candidates=ranked, detected_beats=detected_beats)
        # Transition specs and persisted Clip rows share stable UUIDs; the parser itself stays database-free.
        clip_ids = {item["id"]: str(uuid4()) for item in document["tracks"][0]["clips"]}
        for item in document["tracks"][0]["clips"]:
            item["id"] = clip_ids[item["id"]]
        for item in transitions:
            item["from_clip_id"] = clip_ids[item["from_clip_id"]]
            item["to_clip_id"] = clip_ids[item["to_clip_id"]]
        render_duration = sum(float(item["source_end"]) - float(item["source_start"]) for item in document["tracks"][0]["clips"])
        validate_render_entitlement(user.subscription_tier, render_duration, str(request.get("resolution", "1080p")))
        if user.subscription_tier.value == "free" and user.render_credits <= 0: raise ValueError("免費渲染點數已用完")
        db.query(Timeline).filter(Timeline.project_id == project.id, Timeline.is_current.is_(True)).update({Timeline.is_current: False}, synchronize_session=False)
        next_version = int(db.query(Timeline).filter(Timeline.project_id == project.id).count()) + 1
        timeline = Timeline(project_id=project.id, name=f"一鍵成片・{template.name}", version=next_version, is_current=True)
        settings = {"confirmed_timeline": document, "transition_graph": document["transition_graph"], "one_click": {"status": "assembled", "template_id": template.id, "task_id": self.request.id, "bgm_asset_id": str(request["bgm_asset_id"]) if request.get("bgm_asset_id") else None, "bgm_mix_level": float(template.bgm.get("mix_level", .16)), "detected_beat_count": len(detected_beats or []), "resolution": request.get("resolution", "1080p")}}
        timeline.settings_json = settings; db.add(timeline); db.flush()
        for index, item in enumerate(document["tracks"][0]["clips"]):
            db.add(Clip(id=UUID(item["id"]), timeline_id=timeline.id, source_asset_id=UUID(item["source_asset_id"]), source_start=item["source_start"], source_end=item["source_end"], track=TrackType.MAIN_VIDEO, z_index=0, audio_enabled=True, order_index=index))
        render_job_id = None
        if bool(request.get("auto_render", True)):
            if user.subscription_tier.value == "free": user.render_credits -= 1
            job = RenderJob(project_id=project.id, timeline_id=timeline.id); db.add(job); db.flush(); render_job_id = str(job.id)
        db.commit()
        publish_project_status(project_id, progress=62, stage="one_click_timeline_ready", message="已依節拍組裝 Timeline，正在排入渲染", job_id=render_job_id or self.request.id, extra={"timeline_id": str(timeline.id), "transition_count": len(transitions)})
        if render_job_id:
            render_final_timeline.delay(render_job_id, str(request.get("resolution", "1080p")), template.aspect_ratio)
        else:
            publish_project_status(project_id, progress=100, stage="one_click_completed", status="completed", message="一鍵成片 Timeline 已完成，等待手動導出", job_id=self.request.id, extra={"timeline_id": str(timeline.id)})
        return {"timeline_id": str(timeline.id), "render_job_id": render_job_id or "", "status": "queued" if render_job_id else "completed"}
    except Exception as exc:
        db.rollback()
        if project is not None: publish_project_status(project_id, progress=0, stage="one_click_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
