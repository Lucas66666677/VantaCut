"""Background analysis that turns a BGM plus source material into beat-sync suggestions."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import Clip, MediaAsset, MediaStatus, MediaType, Project, RenderJob, Timeline, TrackType, User
from app.services.beat_sync import BeatSyncError, analyze_music, beat_sync_report, build_beat_montage_document, select_dynamic_window
from app.services.storage import download_object, upload_object
from app.tasks.render_tasks import render_final_timeline
from app.worker import celery_app


def _transition_times(timeline: Timeline) -> list[float]:
    """Read optional output-time transition hints without requiring a specific UI version."""
    graph = dict((timeline.settings_json or {}).get("transition_graph", {}))
    return [
        float(item["output_time"])
        for item in graph.get("transitions", [])
        if item.get("output_time") is not None
    ]


@celery_app.task(bind=True, name="beat_sync.analyze_and_plan")
def analyze_and_plan(self, timeline_id: str, bgm_asset_id: str, source_asset_id: str, max_matches: int = 24, detect_drops: bool = True) -> dict[str, Any]:
    db = SessionLocal()
    timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None:
            raise BeatSyncError("Timeline not found")
        bgm, source = db.get(MediaAsset, UUID(bgm_asset_id)), db.get(MediaAsset, UUID(source_asset_id))
        if bgm is None or source is None:
            raise BeatSyncError("BGM or source asset not found")
        if bgm.project_id != timeline.project_id or source.project_id != timeline.project_id:
            raise BeatSyncError("Assets must belong to the timeline project")

        publish_project_status(str(timeline.project_id), progress=10, stage="beat_sync_preparing", message="正在載入 BGM 與素材代理檔", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"beat-sync-{timeline.id}-") as temporary:
            workdir = Path(temporary)
            bgm_path, video_path = workdir / "bgm-input", workdir / "source.mp4"
            download_object(bgm.audio_key or bgm.storage_key, str(bgm_path))
            download_object(source.proxy_key or source.storage_key, str(video_path))
            publish_project_status(str(timeline.project_id), progress=35, stage="beat_sync_music", message="正在偵測節拍、重拍與高潮", job_id=self.request.id)
            report = beat_sync_report(
                bgm_path,
                video_path,
                transition_times=_transition_times(timeline),
                max_matches=max_matches,
                detect_drops=detect_drops,
            )

        beat_sync = {
            "status": "completed",
            "version": 1,
            "bgm_asset_id": str(bgm.id),
            "source_asset_id": str(source.id),
            "apply_speed_ramps": False,
            **report,
        }
        timeline.settings_json = {**dict(timeline.settings_json or {}), "beat_sync": beat_sync}
        db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="beat_sync_completed", status="completed", message="音樂結構與畫面動量踩點建議已完成", job_id=self.request.id)
        return {"timeline_id": timeline_id, "status": "completed", "match_count": len(report["matches"]), "speed_ramp_count": len(report["speed_ramps"])}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "beat_sync": {"status": "failed", "error": str(exc)}}
                db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="beat_sync_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()


def _photo_to_video(path: Path, output: Path, *, aspect_ratio: str) -> tuple[int, int]:
    width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},format=yuv420p"
    try:
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", str(path), "-t", "5", "-vf", vf, "-r", "30", "-an", "-c:v", "libx264", "-preset", "fast", str(output)], check=True, capture_output=True, text=True, timeout=600)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise BeatSyncError("Unable to package selected photo for beat montage") from exc
    return width, height


@celery_app.task(bind=True, name="beat_sync.generate_montage")
def generate_montage(self, project_id: str, user_id: str, request: dict[str, Any]) -> dict[str, str]:
    db = SessionLocal(); project: Project | None = None
    try:
        project, user = db.get(Project, UUID(project_id)), db.get(User, UUID(user_id))
        if project is None or user is None or project.owner_id != user.id: raise BeatSyncError("Project access changed before montage generation")
        requested = [UUID(str(value)) for value in request["media_asset_ids"]]
        found_assets = db.query(MediaAsset).filter(MediaAsset.id.in_(requested), MediaAsset.project_id == project.id).all()
        assets_by_id = {asset.id: asset for asset in found_assets}
        assets = [assets_by_id[asset_id] for asset_id in requested if asset_id in assets_by_id]
        if len(assets) != len(set(requested)) or any(asset.status != MediaStatus.READY or asset.media_type not in {MediaType.VIDEO, MediaType.IMAGE} for asset in assets): raise BeatSyncError("Select 10-30 ready video or image assets from this project")
        bgm = db.get(MediaAsset, UUID(str(request["bgm_asset_id"])))
        if bgm is None or bgm.project_id != project.id or bgm.status != MediaStatus.READY: raise BeatSyncError("Selected BGM is unavailable")
        aspect_ratio = str(request["aspect_ratio"]); candidates: list[dict[str, Any]] = []
        publish_project_status(project_id, progress=5, stage="beat_montage_preparing", message="正在分析 BGM 強拍與副歌高潮", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"beat-montage-{project_id}-") as temporary:
            workdir = Path(temporary); bgm_path = workdir / "bgm"
            download_object(bgm.audio_key or bgm.storage_key, str(bgm_path)); music = analyze_music(bgm_path)
            for index, asset in enumerate(assets):
                local = workdir / f"media-{index}"; download_object(asset.proxy_key or asset.storage_key, str(local))
                montage_asset = asset; duration = float(asset.duration_seconds or 5)
                if asset.media_type == MediaType.IMAGE:
                    packaged = workdir / f"photo-{index}.mp4"; width, height = _photo_to_video(local, packaged, aspect_ratio=aspect_ratio)
                    key = f"projects/{project.id}/derived/beat-montage/{self.request.id}-{index}.mp4"; upload_object(key, str(packaged), "video/mp4")
                    montage_asset = MediaAsset(project_id=project.id, filename=f"beat-photo-{asset.filename}.mp4", storage_key=key, media_type=MediaType.VIDEO, status=MediaStatus.READY, mime_type="video/mp4", size_bytes=packaged.stat().st_size, duration_seconds=5, width=width, height=height, fps=30, video_codec="h264", metadata_json={"derived_from": str(asset.id), "generation_type": "beat_montage_photo_motion"}); db.add(montage_asset); db.flush(); local = packaged; duration = 5
                source_start, source_end, score = select_dynamic_window(local, duration_seconds=min(2.5, max(.4, 60 / max(music.tempo_bpm or 120, 1))), asset_duration=duration)
                candidates.append({"asset_id": str(montage_asset.id), "asset_duration": duration, "source_start": source_start, "source_end": source_end, "score": score})
                publish_project_status(project_id, progress=12 + int((index + 1) / len(assets) * 58), stage="beat_montage_scoring", message=f"正在挑選高動態素材 {index + 1}/{len(assets)}", job_id=self.request.id)
        document = build_beat_montage_document(music=music, candidates=candidates, aspect_ratio=aspect_ratio)
        # Persist rows so the inspector, audio effects and render path retain normal Timeline semantics.
        for item in document["tracks"][0]["clips"]:
            item["id"] = str(uuid4())
        db.query(Timeline).filter(Timeline.project_id == project.id, Timeline.is_current.is_(True)).update({Timeline.is_current: False}, synchronize_session=False)
        timeline = Timeline(project_id=project.id, name="一鍵卡點 Montage", version=int(db.query(Timeline).filter(Timeline.project_id == project.id).count()) + 1, is_current=True)
        timeline.settings_json = {"confirmed_timeline": document, "multitrack_timeline": document, "beat_sync_montage": {**document["beat_sync_montage"], "status": "assembled", "bgm_asset_id": str(bgm.id), "bgm_mix_level": .24, "bgm_start_offset": float(document["beat_sync_montage"]["timeline_origin"])}}
        db.add(timeline); db.flush()
        for index, item in enumerate(document["tracks"][0]["clips"]): db.add(Clip(id=UUID(item["id"]), timeline_id=timeline.id, source_asset_id=UUID(item["source_asset_id"]), source_start=item["source_start"], source_end=item["source_end"], track=TrackType.MAIN_VIDEO, z_index=0, audio_enabled=False, order_index=index))
        render_job_id = ""
        if request.get("auto_render", False):
            job = RenderJob(project_id=project.id, timeline_id=timeline.id); db.add(job); db.flush(); render_job_id = str(job.id)
        db.commit()
        publish_project_status(project_id, progress=82, stage="beat_montage_ready", message="素材已對齊強拍並加入閃動特效", job_id=render_job_id or self.request.id, extra={"timeline_id": str(timeline.id)})
        if render_job_id: render_final_timeline.delay(render_job_id, str(request["resolution"]), aspect_ratio)
        else: publish_project_status(project_id, progress=100, stage="beat_montage_completed", status="completed", message="卡點 Montage 已準備好審閱", job_id=self.request.id)
        return {"timeline_id": str(timeline.id), "render_job_id": render_job_id, "status": "queued" if render_job_id else "completed"}
    except Exception as exc:
        db.rollback()
        if project is not None: publish_project_status(project_id, progress=0, stage="beat_montage_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally: db.close()
