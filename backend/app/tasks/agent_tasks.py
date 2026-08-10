from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.agent.context import serialise_timeline_state_context
from app.agent.editing_tools import PlannedToolCall, langchain_editing_tools
from app.agent.prompts import EDITING_AGENT_SYSTEM_PROMPT, editing_agent_user_prompt
from app.ai.providers.factory import get_editing_agent_provider
from app.core.ai_retry import retry_ai_task
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import AgentEditRun, AgentEditStatus, MediaAsset, Timeline
from app.services.agent_timeline_versions import apply_planned_tool_calls, clone_timeline_version
from app.worker import celery_app


@celery_app.task(bind=True, name="agent.apply_edit_instruction")
def apply_edit_instruction(self, agent_run_id: str) -> dict[str, Any]:
    """Plan and atomically apply an AI edit as a new Timeline child version."""
    db = SessionLocal()
    run: AgentEditRun | None = None
    try:
        run = db.get(AgentEditRun, UUID(agent_run_id))
        if run is None:
            raise ValueError("Agent edit run not found")
        source = db.get(Timeline, run.source_timeline_id)
        if source is None:
            raise ValueError("Source Timeline not found")
        run.status = AgentEditStatus.PLANNING
        db.commit()
        publish_project_status(
            str(run.project_id), progress=15, stage="agent_planning", message="AI 正在理解目前時間軸",
            job_id=self.request.id,
        )

        assets = db.scalars(
            select(MediaAsset).where(MediaAsset.project_id == run.project_id).order_by(MediaAsset.created_at.desc())
        ).all()
        provider = get_editing_agent_provider()
        context = serialise_timeline_state_context(source, assets)
        raw_calls, clarification = provider.plan_edit(
            system_prompt=EDITING_AGENT_SYSTEM_PROMPT,
            user_prompt=editing_agent_user_prompt(run.instruction, context),
            tools=langchain_editing_tools(),
        )
        calls = [PlannedToolCall.model_validate(item) for item in raw_calls]
        run.provider_name = provider.name
        run.tool_calls_json = [call.as_json() for call in calls]
        if not calls:
            run.status = AgentEditStatus.COMPLETED
            run.error_message = clarification
            db.commit()
            publish_project_status(
                str(run.project_id), progress=100, stage="agent_completed", status="completed",
                message=clarification or "沒有需要套用的安全修改", job_id=self.request.id,
            )
            return {"agent_run_id": agent_run_id, "timeline_id": None, "tool_calls": 0, "message": clarification}

        publish_project_status(
            str(run.project_id), progress=60, stage="agent_applying", message="正在建立可復原的時間軸版本",
            job_id=self.request.id,
        )
        # Lock immediately before applying. A stale conversational plan must not overwrite a newer human edit.
        source = db.scalar(select(Timeline).where(Timeline.id == run.source_timeline_id).with_for_update())
        if source is None or not source.is_current:
            raise ValueError("Timeline changed after planning; please retry from the latest version")
        run.status = AgentEditStatus.APPLYING
        target = clone_timeline_version(db, source, label=f"AI edit: {source.name}")
        apply_planned_tool_calls(db, target, calls)
        source.is_current = False
        target.is_current = True
        run.result_timeline_id = target.id
        run.status = AgentEditStatus.COMPLETED
        run.error_message = clarification
        db.commit()
        publish_project_status(
            str(run.project_id), progress=100, stage="agent_completed", status="completed",
            message="AI 修改已建立為新的可復原版本", job_id=self.request.id,
        )
        return {"agent_run_id": agent_run_id, "timeline_id": str(target.id), "tool_calls": len(calls)}
    except Exception as exc:
        db.rollback()
        if run is not None and retry_ai_task(
            self, exc, project_id=str(run.project_id), stage="agent_planning",
            message="AI 編輯服務暫時不可用", job_id=self.request.id,
        ):
            raise AssertionError("retry_ai_task either raises or returns False")
        if run is not None:
            current = db.get(AgentEditRun, run.id)
            if current is not None:
                current.status = AgentEditStatus.FAILED
                current.error_message = str(exc)
                db.commit()
            publish_project_status(
                str(run.project_id), progress=0, stage="agent_failed", status="failed", message=str(exc),
                job_id=self.request.id,
            )
        raise
    finally:
        db.close()
