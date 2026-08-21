from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.ai.providers.factory import get_voice_clone_provider
from app.db.session import get_db
from app.models.entities import MediaAsset, Project, Timeline, User, VoiceProfile, VoiceProfileStatus
from app.schemas.voice import CreateVoiceProfileRequest, GenerateVoiceReplacementRequest, VoiceMorphRequest, VoiceMorphResponse, VoiceProfileResponse, VoiceReplacementResponse
from app.tasks.voice_tasks import extract_voice_profile, generate_voice_morph, generate_voice_replacement


router = APIRouter(tags=["voice-cloning"])


def _owned_project(db: Session, project_id: UUID, current_user: User) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot access this project")
    return project


@router.post("/projects/{project_id}/voice-profiles", response_model=VoiceProfileResponse, status_code=status.HTTP_202_ACCEPTED)
def create_voice_profile(project_id: UUID, payload: CreateVoiceProfileRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> VoiceProfileResponse:
    _owned_project(db, project_id, current_user)
    asset = db.get(MediaAsset, payload.source_media_asset_id)
    if asset is None or asset.project_id != project_id or not asset.audio_key:
        raise HTTPException(status_code=422, detail="A project media asset with preprocessed audio is required")
    profile = VoiceProfile(project_id=project_id, created_by_id=current_user.id, source_media_asset_id=asset.id, name=payload.name, provider_name=get_voice_clone_provider().name, status=VoiceProfileStatus.QUEUED, language=payload.language, metadata_json={"consent_confirmed": True})
    db.add(profile); db.commit(); db.refresh(profile)
    task = extract_voice_profile.delay(str(profile.id))
    return VoiceProfileResponse(id=profile.id, project_id=project_id, source_media_asset_id=asset.id, name=profile.name, status="queued", provider_name=profile.provider_name, task_id=task.id)


@router.get("/projects/{project_id}/voice-profiles", response_model=list[VoiceProfileResponse])
def list_voice_profiles(project_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[VoiceProfileResponse]:
    _owned_project(db, project_id, current_user)
    profiles = db.scalars(select(VoiceProfile).where(VoiceProfile.project_id == project_id).order_by(VoiceProfile.created_at.desc())).all()
    return [VoiceProfileResponse(id=item.id, project_id=item.project_id, source_media_asset_id=item.source_media_asset_id, name=item.name, status=item.status.value, provider_name=item.provider_name, quality_score=float(item.quality_score) if item.quality_score is not None else None) for item in profiles]


@router.post("/timelines/{timeline_id}/voice-replacements", response_model=VoiceReplacementResponse, status_code=status.HTTP_202_ACCEPTED)
def request_voice_replacement(timeline_id: UUID, payload: GenerateVoiceReplacementRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> VoiceReplacementResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    _owned_project(db, timeline.project_id, current_user)
    profile = db.get(VoiceProfile, payload.voice_profile_id)
    if profile is None or profile.project_id != timeline.project_id or profile.status != VoiceProfileStatus.READY:
        raise HTTPException(status_code=422, detail="A ready project voice profile is required")
    cue_ids = {str(item.get("id")) for item in dict(timeline.settings_json.get("subtitles", {})).get("items", [])}
    if payload.cue_id not in cue_ids:
        raise HTTPException(status_code=422, detail="cue_id must reference a generated subtitle cue")
    task = generate_voice_replacement.delay(str(timeline.id), payload.model_dump(mode="json"))
    return VoiceReplacementResponse(task_id=task.id, timeline_id=timeline.id, voice_profile_id=profile.id, status="queued")


@router.post("/timelines/{timeline_id}/voice-morphs", response_model=VoiceMorphResponse, status_code=status.HTTP_202_ACCEPTED)
def request_voice_morph(timeline_id: UUID, payload: VoiceMorphRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> VoiceMorphResponse:
    timeline, asset = db.get(Timeline, timeline_id), db.get(MediaAsset, payload.source_media_asset_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    _owned_project(db, timeline.project_id, current_user)
    if asset is None or asset.project_id != timeline.project_id or not asset.audio_key:
        raise HTTPException(status_code=422, detail="Select a project media asset with preprocessed audio")
    if asset.duration_seconds is not None and payload.source_end > float(asset.duration_seconds) + .05:
        raise HTTPException(status_code=422, detail="Voice-morph source range exceeds the source asset")
    settings = dict(timeline.settings_json or {})
    settings["voice_morph_status"] = {"status": "queued", "character_id": payload.character_id}
    timeline.settings_json = settings; db.commit()
    task = generate_voice_morph.delay(str(timeline.id), payload.model_dump(mode="json"))
    return VoiceMorphResponse(task_id=task.id, timeline_id=timeline.id, status="queued")
