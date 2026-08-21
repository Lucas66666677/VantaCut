from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import AgentEditRun, AgentEditStatus, Timeline, User
from app.schemas.agent import (
    AgentEditRequest, AgentEditResponse, AgentEditRunResponse, AgentPreviewRequest, AgentPreviewResponse,
    UndoTimelineRequest, UndoTimelineResponse,
)
from app.agent.editing_tools import PlannedToolCall, langchain_editing_tools
from app.agent.prompts import EDITING_AGENT_SYSTEM_PROMPT, editing_agent_user_prompt
from app.ai.providers.factory import get_editing_agent_provider
from app.tasks.agent_tasks import apply_edit_instruction


router = APIRouter(tags=["editing-agent"])


def _authorise_timeline(db: Session, timeline_id: UUID, current_user: User) -> Timeline:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot edit this Timeline")
    return timeline


@router.post("/timelines/{timeline_id}/agent-preview", response_model=AgentPreviewResponse)
def preview_agent_edit(
    timeline_id: UUID, payload: AgentPreviewRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> AgentPreviewResponse:
    """Return validated tool calls without touching Timeline records or creating a version.

    The browser supplies a deliberately compact state-tree snapshot so the
    proposal always targets the exact local Zustand state the editor is showing.
    """
    timeline = _authorise_timeline(db, timeline_id, current_user)
    if not timeline.is_current:
        raise HTTPException(status_code=409, detail="Please plan from the current Timeline version")
    provider = get_editing_agent_provider()
    context = json.dumps(payload.timeline_context, ensure_ascii=False, separators=(",", ":"))
    try:
        raw_calls, clarification = provider.plan_edit(
            system_prompt=EDITING_AGENT_SYSTEM_PROMPT,
            user_prompt=editing_agent_user_prompt(payload.instruction, context),
            tools=langchain_editing_tools(),
        )
        calls = [PlannedToolCall.model_validate(item).as_json() for item in raw_calls]
    except Exception as exc:
        raise HTTPException(status_code=503, detail="AI editing planner is temporarily unavailable") from exc
    return AgentPreviewResponse(provider_name=provider.name, tool_calls=calls, explanation=clarification)


@router.post("/timelines/{timeline_id}/agent-edits", response_model=AgentEditResponse, status_code=status.HTTP_202_ACCEPTED)
def create_agent_edit(
    timeline_id: UUID, payload: AgentEditRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> AgentEditResponse:
    timeline = _authorise_timeline(db, timeline_id, current_user)
    if not timeline.is_current:
        raise HTTPException(status_code=409, detail="Please start the AI edit from the current Timeline version")
    run = AgentEditRun(
        project_id=timeline.project_id, source_timeline_id=timeline.id,
        instruction=payload.instruction, status=AgentEditStatus.QUEUED,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        task = apply_edit_instruction.delay(str(run.id))
    except Exception as exc:
        run.status = AgentEditStatus.FAILED
        run.error_message = f"Unable to enqueue Agent edit: {exc}"
        db.commit()
        raise HTTPException(status_code=503, detail="AI edit queue is temporarily unavailable") from exc
    return AgentEditResponse(agent_run_id=run.id, task_id=task.id, source_timeline_id=timeline.id, status=run.status.value)


@router.get("/agent-edits/{agent_run_id}", response_model=AgentEditRunResponse)
def get_agent_edit(
    agent_run_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> AgentEditRunResponse:
    run = db.get(AgentEditRun, agent_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent edit run not found")
    if run.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot view this Agent edit")
    return AgentEditRunResponse(
        id=run.id, source_timeline_id=run.source_timeline_id, result_timeline_id=run.result_timeline_id,
        status=run.status.value, provider_name=run.provider_name, tool_calls=run.tool_calls_json,
        message=run.error_message,
    )


@router.post("/timelines/{timeline_id}/undo", response_model=UndoTimelineResponse)
def undo_agent_timeline_version(
    timeline_id: UUID, payload: UndoTimelineRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> UndoTimelineResponse:
    current = _authorise_timeline(db, timeline_id, current_user)
    if not current.is_current:
        raise HTTPException(status_code=409, detail="Only the current Timeline version can be undone")
    if current.parent_timeline_id is None:
        raise HTTPException(status_code=409, detail="This Timeline has no prior version to restore")
    parent = db.scalar(select(Timeline).where(Timeline.id == current.parent_timeline_id).with_for_update())
    if parent is None:
        raise HTTPException(status_code=409, detail="The prior Timeline version is unavailable")
    current.is_current = False
    parent.is_current = True
    db.commit()
    return UndoTimelineResponse(current_timeline_id=parent.id, restored_from_timeline_id=current.id, version=parent.version)
