from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai.providers.base import TextAnalysisProvider
from app.ai.providers.factory import get_text_provider
from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import Timeline, User
from app.schemas.nudge import NudgeRequest, NudgeResponse
from app.services.non_destructive import append_filter_layer
from app.services.nudge_commands import plan_nudge


router = APIRouter(prefix="/timelines", tags=["nudge-commands"])


def text_provider_dependency() -> TextAnalysisProvider:
    return get_text_provider()


@router.post("/{timeline_id}/nudge", response_model=NudgeResponse)
def nudge_timeline(
    timeline_id: UUID,
    payload: NudgeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NudgeResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")

    provider = text_provider_dependency()
    commands, explanation, provider_name = plan_nudge(provider, instruction=payload.instruction, target_clip_ids=payload.target_clip_ids)
    if not commands:
        return NudgeResponse(timeline_id=timeline.id, provider_name=provider_name, commands=[], explanation=explanation)

    settings = dict(timeline.settings_json or {})
    audit = list(settings.get("nudge_command_log") or [])[-49:]
    serialised_commands = [command.model_dump(mode="json") for command in commands]
    audit.append({"instruction": payload.instruction, "commands": serialised_commands})
    settings["nudge_command_log"] = audit
    timeline.settings_json = append_filter_layer(
        settings,
        kind="nudge_command",
        target={"timeline_id": str(timeline.id), "clip_ids": payload.target_clip_ids},
        parameters={"instruction": payload.instruction, "commands": serialised_commands},
        source="ai",
    )
    db.commit()
    return NudgeResponse(timeline_id=timeline.id, provider_name=provider_name, commands=commands, explanation=explanation)
