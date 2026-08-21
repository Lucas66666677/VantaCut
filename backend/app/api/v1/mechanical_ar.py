import uuid
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import MediaAsset, Timeline, User
from app.schemas.mechanical_ar import MechanicalARRequest, MechanicalARUploadResponse, MechanicalARTaskResponse
from app.services.storage import upload_bytes
from app.tasks.mechanical_ar_tasks import analyze_mechanical_timeline


router = APIRouter(prefix="/timelines", tags=["mechanical-ar"])
MAX_CODE_BYTES = 1_000_000


def _authorise(timeline_id: UUID, current_user: User, db: Session) -> Timeline:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot use mechanical AR on this timeline")
    return timeline


@router.post("/{timeline_id}/mechanical-ar/code", response_model=MechanicalARUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_mechanical_program(
    timeline_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MechanicalARUploadResponse:
    timeline = _authorise(timeline_id, current_user, db)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in {".py", ".hex"}:
        raise HTTPException(status_code=422, detail="Only .py and Intel HEX .hex program files are accepted")
    payload = await file.read(MAX_CODE_BYTES + 1)
    if not payload or len(payload) > MAX_CODE_BYTES:
        raise HTTPException(status_code=413, detail="Program file must be between 1 byte and 1 MB")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Program files must be UTF-8 text") from exc
    code_asset_id = str(uuid.uuid4()); language = "python" if extension == ".py" else "intel_hex"
    key = f"projects/{timeline.project_id}/timelines/{timeline.id}/mechanical-code/{code_asset_id}{extension}"
    upload_bytes(key, payload, "text/x-python" if extension == ".py" else "text/plain")
    settings = dict(timeline.settings_json or {}); code_assets = list(settings.get("mechanical_code_assets", []))
    code_assets.append({"id": code_asset_id, "filename": Path(file.filename or f"program{extension}").name, "extension": extension, "language": language, "storage_key": key})
    timeline.settings_json = {**settings, "mechanical_code_assets": code_assets}
    db.commit()
    return MechanicalARUploadResponse(code_asset_id=code_asset_id, timeline_id=timeline.id, filename=code_assets[-1]["filename"], language=language)


@router.post("/{timeline_id}/mechanical-ar/analyze", response_model=MechanicalARTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def request_mechanical_ar_analysis(timeline_id: UUID, payload: MechanicalARRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MechanicalARTaskResponse:
    timeline = _authorise(timeline_id, current_user, db)
    asset = db.get(MediaAsset, payload.media_asset_id)
    if asset is None or asset.project_id != timeline.project_id:
        raise HTTPException(status_code=404, detail="Media asset was not found in this Timeline project")
    if payload.code_asset_id and not any(item.get("id") == payload.code_asset_id for item in dict(timeline.settings_json or {}).get("mechanical_code_assets", [])):
        raise HTTPException(status_code=404, detail="Mechanical program file not found on this timeline")
    timeline.settings_json = {**dict(timeline.settings_json or {}), "mechanical_ar": {"status": "queued", "media_asset_id": str(asset.id), "code_asset_id": payload.code_asset_id}}
    db.commit()
    task = analyze_mechanical_timeline.delay(str(timeline.id), str(asset.id), payload.code_asset_id, payload.use_proxy, payload.sample_fps, payload.vocabulary)
    return MechanicalARTaskResponse(task_id=task.id, timeline_id=timeline.id, status="queued")
