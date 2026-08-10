"""Fetch allowlisted market data, calculate indicators, and create Timeline-bound finance tracks."""
from __future__ import annotations

import copy
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import Timeline
from app.services.finance_chart import render_finance_chart_rgba
from app.services.finance_data import enrich_indicators, fetch_history_cached
from app.services.storage import upload_object
from app.worker import celery_app


def _fetch(track: dict[str, Any]) -> list[dict[str, Any]]:
    start, end = date.fromisoformat(track["history_start"]), date.fromisoformat(track["history_end"])
    candles = fetch_history_cached(track["market"], track["symbol"], start, end)
    return enrich_indicators(candles)


@celery_app.task(bind=True, name="finance.refresh_track")
def refresh_finance_track(self, timeline_id: str, finance_track_id: str) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id))
        if timeline is None: raise RuntimeError("Timeline not found")
        settings = copy.deepcopy(dict(timeline.settings_json or {})); tracks = list(settings.get("finance_tracks", [])); index = next((i for i, item in enumerate(tracks) if item.get("id") == finance_track_id), None)
        if index is None: raise RuntimeError("Finance track not found")
        track = dict(tracks[index]); publish_project_status(str(timeline.project_id), progress=20, stage="finance_data_fetch", message="正在取得市場歷史報價與技術指標", job_id=self.request.id)
        candles = _fetch(track)
        source_notice = "TWSE 日資料（收盤資料，非即時報價）。" if track["market"] == "twse" else "授權相容供應商資料；即時性與再散布權利依供應商合約為準。"
        track["candles"], track["data_as_of"], track["data_notice"] = candles, candles[-1]["timestamp"], f"{source_notice} 技術指標由 OHLCV 計算，僅供教學視覺化，非投資建議。"
        with tempfile.TemporaryDirectory(prefix=f"finance-{finance_track_id}-") as temporary:
            workdir = Path(temporary); rgba = workdir / "finance-alpha.mov"; render_finance_chart_rgba(track, rgba)
            key = f"projects/{timeline.project_id}/timelines/{timeline.id}/finance/{finance_track_id}/finance-alpha.mov"; upload_object(key, str(rgba), "video/quicktime")
        completed = {**track, "status": "completed", "rgba_video_key": key}; tracks[index] = completed; settings["finance_tracks"] = tracks
        timeline.settings_json = settings; db.commit(); publish_project_status(str(timeline.project_id), progress=100, stage="finance_track_completed", status="completed", message="金融軌道與動態 K 線圖已生成", job_id=self.request.id)
        return {"finance_track_id": finance_track_id, "candles": len(candles), "rgba_video_key": key}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            current = db.get(Timeline, timeline.id)
            if current is not None:
                settings = copy.deepcopy(dict(current.settings_json or {})); settings["finance_tracks"] = [{**item, "status": "failed", "error": str(exc)} if item.get("id") == finance_track_id else item for item in settings.get("finance_tracks", [])]; current.settings_json = settings; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="finance_track_failed", status="failed", message="金融資料取得或 K 線圖生成失敗", job_id=self.request.id)
        raise
    finally:
        db.close()
