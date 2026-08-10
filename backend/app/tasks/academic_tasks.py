from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.ai.academic_prompts import ACADEMIC_NARRATIVE_SYSTEM_PROMPT, academic_narrative_prompt, academic_narrative_response_schema
from app.ai.providers.factory import get_text_provider
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import Timeline
from app.schemas.academic import AcademicGlossaryEntry
from app.schemas.subtitle import SubtitleCue
from app.services.academic import academic_lut, apply_glossary_to_cues, validate_narrative_plan
from app.services.agent_timeline_versions import clone_timeline_version
from app.services.storage import upload_bytes, upload_object
from app.services.subtitles import cues_to_ass, cues_to_srt
from app.worker import celery_app


@celery_app.task(bind=True, name="academic.assemble_timeline")
def assemble_academic_timeline(self, timeline_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); source: Timeline | None = None
    try:
        source = db.get(Timeline, UUID(timeline_id))
        if source is None:
            raise ValueError("Timeline not found")
        settings = copy.deepcopy(dict(source.settings_json or {})); subtitle_data = dict(settings.get("subtitles", {}))
        if subtitle_data.get("status") != "completed" or not subtitle_data.get("items"):
            raise ValueError("Generate timestamped subtitles before assembling an academic narrative")
        glossary = [AcademicGlossaryEntry.model_validate(item) for item in request.get("glossary", [])]
        cues = [SubtitleCue.model_validate(item) for item in subtitle_data["items"]]
        cues, glossary_review = apply_glossary_to_cues(cues, glossary)
        transcript = [{"start_time": cue.start_time, "end_time": cue.end_time, "text": cue.text} for cue in cues]
        publish_project_status(str(source.project_id), progress=25, stage="academic_narrative", message="正在依學術敘事模板規劃研究動機、方法、結果與展望", job_id=self.request.id)
        raw_plan = get_text_provider().generate_structured_json(
            system_prompt=ACADEMIC_NARRATIVE_SYSTEM_PROMPT,
            user_prompt=academic_narrative_prompt(transcript=transcript, target_programmes=list(request.get("target_programmes", []))),
            response_schema=academic_narrative_response_schema(),
        )
        plan = validate_narrative_plan(raw_plan)
        target = clone_timeline_version(db, source, label=f"Academic review: {source.name}")
        target_settings = copy.deepcopy(dict(target.settings_json or {})); base = f"projects/{target.project_id}/timelines/{target.id}/academic"
        corrected_srt, corrected_ass = f"{base}/subtitles-academic.srt", f"{base}/subtitles-academic.ass"
        upload_bytes(corrected_srt, cues_to_srt(cues).encode("utf-8"), "application/x-subrip")
        upload_bytes(corrected_ass, cues_to_ass(cues).encode("utf-8"), "text/x-ssa")
        lut_key: str | None = None
        if request.get("apply_academic_lut", True):
            with tempfile.TemporaryDirectory(prefix=f"academic-lut-{target.id}-") as temporary:
                lut_path = academic_lut(Path(temporary) / "academic-neutral.cube")
                lut_key = f"{base}/academic-neutral.cube"; upload_object(lut_key, str(lut_path), "text/plain")
            target_settings["color_lut"] = {"lut_key": lut_key, "intensity": .82, "preset": "academic_neutral"}
            target_settings["approved_lut_keys"] = list(dict.fromkeys([*list(target_settings.get("approved_lut_keys", [])), lut_key]))
        target_settings["subtitles"] = {**subtitle_data, "items": [cue.model_dump(mode="json") for cue in cues], "srt_key": corrected_srt, "ass_key": corrected_ass, "academic_glossary_review": glossary_review}
        target_settings["academic_mode"] = {"status": "completed", "template_id": "research_pitch_v1", "narrative_plan": plan, "glossary": [item.model_dump(mode="json") for item in glossary], "glossary_review": glossary_review, "academic_lut_key": lut_key, "delivery": {"enabled": True, "tempo": float(request["speech_tempo"]), "disclaimer": "Conservative EQ, compression and global A/V-synchronised tempo adjustment; this does not measure confidence or admission readiness."}}
        target.settings_json = target_settings; db.commit()
        source.settings_json = {**dict(source.settings_json or {}), "academic_mode": {"status": "completed", "review_timeline_id": str(target.id), "template_id": "research_pitch_v1"}}
        db.commit()
        publish_project_status(str(source.project_id), progress=100, stage="academic_completed", status="completed", message="學術敘事、術語保護、LUT 與沉穩化導出版本已建立，等待審閱", job_id=self.request.id)
        return {"source_timeline_id": timeline_id, "result_timeline_id": str(target.id), "glossary_review_count": len(glossary_review), "lut_key": lut_key}
    except Exception as exc:
        db.rollback()
        if source is not None:
            current = db.get(Timeline, source.id)
            if current is not None:
                current.settings_json = {**dict(current.settings_json or {}), "academic_mode": {"status": "failed", "error": str(exc)}}; db.commit()
            publish_project_status(str(source.project_id), progress=0, stage="academic_failed", status="failed", message="學術影片組裝失敗", job_id=self.request.id)
        raise
    finally:
        db.close()
