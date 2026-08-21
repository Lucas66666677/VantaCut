from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import Timeline, User
from app.schemas.narration_tts import GenerateNarrationRequest, GenerateNarrationResponse, NarrationStyleResponse
from app.services.narration_tts import NARRATION_STYLES
from app.tasks.narration_tasks import generate_tts_narration


router = APIRouter(prefix="/timelines", tags=["narration-tts"])


@router.get("/narration-styles", response_model=list[NarrationStyleResponse])
def list_narration_styles() -> list[NarrationStyleResponse]:
    descriptions = {
        "energetic_girl": "明亮、活潑的短影音開場", "calm_narrator": "沉穩清晰的知識解說", "funny_host": "帶有玩心的幽默節奏",
        "warm_friend": "親切暖心的分享口吻", "cool_storyteller": "冷靜、有電影感的敘事",
    }
    return [NarrationStyleResponse(id=style_id, label=style["label"], description=descriptions[style_id]) for style_id, style in NARRATION_STYLES.items()]  # type: ignore[list-item]


@router.post("/{timeline_id}/narrations", response_model=GenerateNarrationResponse, status_code=status.HTTP_202_ACCEPTED)
def request_tts_narration(timeline_id: UUID, payload: GenerateNarrationRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> GenerateNarrationResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    narration_id = str(uuid4()); request = payload.model_dump(mode="json")
    settings = dict(timeline.settings_json or {}); narrations = list(settings.get("tts_narrations", []))
    narrations.append({"id": narration_id, "status": "queued", **request})
    timeline.settings_json = {**settings, "tts_narrations": narrations[-100:]}; db.commit()
    task = generate_tts_narration.delay(str(timeline.id), narration_id, request)
    return GenerateNarrationResponse(task_id=task.id, timeline_id=timeline.id, narration_id=narration_id, status="queued")
