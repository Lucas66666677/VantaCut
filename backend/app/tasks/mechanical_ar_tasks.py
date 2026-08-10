from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, Timeline
from app.schemas.mechanical_ar import DEFAULT_PART_VOCABULARY
from app.services.mechanical_ar import analyze_mechanical_video, parse_program_actions, project_effects_to_timeline
from app.services.storage import download_object
from app.worker import celery_app


class MechanicalARTaskError(RuntimeError):
    pass


def _main_segments(document: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    for track in document.get("tracks", []):
        if track.get("type") == "main_video":
            return [dict(clip) for clip in track.get("clips", [])]
    segments = [dict(item) for item in document.get("segments", [])]
    return segments or [{"source_start": 0.0, "source_end": duration, "action": "keep"}]


@celery_app.task(bind=True, name="mechanical_ar.analyze_timeline")
def analyze_mechanical_timeline(
    self, timeline_id: str, media_asset_id: str, code_asset_id: str | None,
    use_proxy: bool = True, sample_fps: float = 4.0, vocabulary: list[str] | None = None,
) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id)); asset = db.get(MediaAsset, UUID(media_asset_id))
        if timeline is None or asset is None or asset.project_id != timeline.project_id:
            raise MechanicalARTaskError("Timeline or source media asset is invalid")
        settings = copy.deepcopy(dict(timeline.settings_json or {})); confirmed = dict(settings.get("confirmed_timeline", {}))
        source_key = asset.proxy_key if use_proxy and asset.proxy_key else asset.storage_key
        code_asset = next((dict(item) for item in settings.get("mechanical_code_assets", []) if item.get("id") == code_asset_id), None)
        publish_project_status(str(timeline.project_id), progress=10, stage="mechanical_ar_preparing", message="正在準備 STEM 素材、元件詞彙與程式碼", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"mechanical-ar-{timeline.id}-") as temporary:
            workdir = Path(temporary); source = workdir / "source.mp4"; download_object(source_key, str(source))
            program = {"language": None, "actions": [], "notice": "No source code was supplied; AR motion cues will not have code highlights."}
            if code_asset:
                code_path = workdir / f"program{code_asset['extension']}"; download_object(str(code_asset["storage_key"]), str(code_path))
                program = parse_program_actions(code_path.read_text(encoding="utf-8"), str(code_asset["extension"]))
            publish_project_status(str(timeline.project_id), progress=35, stage="mechanical_part_tracking", message="正在以零樣本詞彙辨識元件並計算光流", job_id=self.request.id)
            report = analyze_mechanical_video(source, vocabulary=vocabulary or list(DEFAULT_PART_VOCABULARY), sample_fps=sample_fps)
        segments = _main_segments(confirmed, float(asset.duration_seconds or 0))
        report["effects"] = project_effects_to_timeline(report.pop("visual_effects"), segments, list(program["actions"]))
        report.update({"status": "completed", "code": program, "source": "proxy" if source_key == asset.proxy_key else "original", "media_asset_id": str(asset.id), "code_asset_id": code_asset_id})
        # Keep a renderer-neutral Timeline projection available for human review/versioning.
        confirmed["mechanical_ar_effects"] = report["effects"]
        settings["confirmed_timeline"] = confirmed; settings["multitrack_timeline"] = confirmed; settings["mechanical_ar"] = report
        timeline.settings_json = settings; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="mechanical_ar_completed", status="completed", message=f"已產生 {len(report['effects'])} 個機械／電路教學 AR 效果", job_id=self.request.id)
        return {"timeline_id": timeline_id, "effect_count": len(report["effects"]), "parts": len(report["part_observations"])}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "mechanical_ar": {"status": "failed", "error": str(exc)}}; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="mechanical_ar_failed", status="failed", message="機械／電路追蹤失敗", job_id=self.request.id)
        raise
    finally:
        db.close()
