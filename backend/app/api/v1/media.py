from pathlib import PurePath

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.ai.providers.base import EmbeddingProvider
from app.ai.providers.factory import get_embedding_provider
from app.models.entities import MediaAsset, MediaEmbeddingSegment, MediaStatus, MediaType, User
from app.schemas.media import (
    ConfirmUploadRequest,
    MediaAssetResponse,
    MultipartCompleteRequest,
    MultipartPartURLRequest,
    MultipartPartURLResponse,
    MultipartUploadInitiateResponse,
    UploadURLRequest,
    UploadURLResponse,
)
from app.services.storage import complete_multipart_upload, create_multipart_part_url, create_multipart_upload, create_upload_url, object_exists
from app.schemas.semantic_search import MediaSemanticGridItem, MediaSemanticGridRequest, MediaSemanticGridResponse, MediaSemanticSearchRequest, MediaSemanticSearchResponse, MediaSemanticSearchResult
from app.tasks.media_tasks import process_new_media
from app.schemas.derived_previews import DerivedPreviewResponse
from app.services.storage import create_download_url

router = APIRouter(prefix="/media", tags=["media"])


def embedding_provider_dependency() -> EmbeddingProvider:
    return get_embedding_provider()


@router.get("/{media_asset_id}/derived-previews/{job_id}", response_model=DerivedPreviewResponse)
def get_derived_preview(media_asset_id: str, job_id: str, user_id: str, db: Session = Depends(get_db)) -> DerivedPreviewResponse:
    """Return a short-lived URL only after an optimistic media operation has really completed."""
    from uuid import UUID

    try:
        asset_id, owner_id = UUID(media_asset_id), UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid media asset or user id") from exc
    asset, user = db.get(MediaAsset, asset_id), db.get(User, owner_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if user is None or asset.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot view this derived preview")
    metadata = dict(asset.metadata_json or {})
    for kind, history_key, output_key in (("inpainting", "video_inpainting_jobs", "output_key"), ("matting", "matting_jobs", "alpha_webm_key")):
        record = next((dict(item) for item in reversed(list(metadata.get(history_key, []))) if isinstance(item, dict) and str(item.get("job_id")) == job_id), None)
        if record is None:
            continue
        key = record.get(output_key)
        return DerivedPreviewResponse(media_asset_id=asset.id, job_id=job_id, kind=kind, status=str(record.get("status", "processing")), preview_url=create_download_url(str(key)) if record.get("status") == "completed" and key else None, error=record.get("error"))
    return DerivedPreviewResponse(media_asset_id=asset.id, job_id=job_id, kind="inpainting", status="processing")


@router.post("/search", response_model=MediaSemanticSearchResponse)
def search_media_semantically(
    payload: MediaSemanticSearchRequest,
    db: Session = Depends(get_db),
    provider: EmbeddingProvider = Depends(embedding_provider_dependency),
) -> MediaSemanticSearchResponse:
    query_embedding = provider.embed_text(payload.query)
    distance = MediaEmbeddingSegment.embedding.cosine_distance(query_embedding).label("distance")
    rows = db.execute(
        select(MediaEmbeddingSegment, MediaAsset, distance)
        .join(MediaAsset, MediaEmbeddingSegment.media_asset_id == MediaAsset.id)
        .where(MediaAsset.project_id == payload.project_id, MediaAsset.status == MediaStatus.READY)
        .order_by(distance)
        .limit(payload.limit)
    ).all()
    results = [
        MediaSemanticSearchResult(
            media_asset_id=asset.id,
            filename=asset.filename,
            thumbnail_key=asset.thumbnail_key,
            thumbnail_url=create_download_url(asset.thumbnail_key) if asset.thumbnail_key else None,
            source_duration=float(asset.duration_seconds) if asset.duration_seconds is not None else None,
            source_start=float(segment.source_start),
            source_end=float(segment.source_end),
            modality=segment.modality,  # type: ignore[arg-type]
            similarity_score=max(0.0, min(1.0, 1.0 - float(row_distance))),
            matched_text=segment.metadata_json.get("text"),
        )
        for segment, asset, row_distance in rows
    ]
    return MediaSemanticSearchResponse(query=payload.query, results=results)


@router.post("/semantic-grid", response_model=MediaSemanticGridResponse)
def semantic_media_grid(payload: MediaSemanticGridRequest, db: Session = Depends(get_db)) -> MediaSemanticGridResponse:
    """Return a bounded 2-D random projection of pgvector asset embeddings for a client-side force layout."""
    import math

    assets = db.scalars(
        select(MediaAsset)
        .where(MediaAsset.project_id == payload.project_id, MediaAsset.status == MediaStatus.READY, MediaAsset.embedding.is_not(None))
        .order_by(MediaAsset.created_at.desc()).limit(payload.limit)
    ).all()
    raw: list[tuple[MediaAsset, float, float]] = []
    for asset in assets:
        vector = list(asset.embedding or [])
        # Deterministic random projection keeps stable neighbourhoods without storing a second UMAP layout.
        x = sum(float(value) * math.sin((index + 1) * 1.618) for index, value in enumerate(vector[:128]))
        y = sum(float(value) * math.cos((index + 1) * 2.414) for index, value in enumerate(vector[:128]))
        raw.append((asset, x, y))
    max_x = max((abs(x) for _, x, _ in raw), default=1.0) or 1.0
    max_y = max((abs(y) for _, _, y in raw), default=1.0) or 1.0
    return MediaSemanticGridResponse(items=[
        MediaSemanticGridItem(
            media_asset_id=asset.id, filename=asset.filename,
            thumbnail_url=create_download_url(asset.thumbnail_key) if asset.thumbnail_key else None,
            source_end=min(float(asset.duration_seconds or 4), 4),
            cluster_x=max(0, min(1, .5 + x / (2 * max_x))), cluster_y=max(0, min(1, .5 + y / (2 * max_y))),
            cluster_label=str((asset.metadata_json or {}).get("semantic_index", {}).get("dominant_scene", "視覺相近素材")),
        ) for asset, x, y in raw
    ])


@router.post("/upload-url", response_model=UploadURLResponse, status_code=status.HTTP_201_CREATED)
def create_media_upload_url(
    payload: UploadURLRequest, db: Session = Depends(get_db)
) -> UploadURLResponse:
    asset = _create_uploading_asset(payload, db)
    return UploadURLResponse(
        asset_id=asset.id,
        storage_key=asset.storage_key,
        upload_url=create_upload_url(asset.storage_key, payload.content_type),
        expires_in=settings.presigned_url_expire_seconds,
        required_headers={"Content-Type": payload.content_type},
    )


def _create_uploading_asset(payload: UploadURLRequest, db: Session) -> MediaAsset:
    # Project ownership/authentication can be added here when auth middleware is enabled.
    safe_filename = PurePath(payload.filename).name
    storage_key = f"projects/{payload.project_id}/original/{asset_id_placeholder(safe_filename)}"
    asset = MediaAsset(
        project_id=payload.project_id,
        filename=safe_filename,
        storage_key=storage_key,
        media_type=payload.media_type,
        mime_type=payload.content_type,
        size_bytes=payload.size_bytes,
        status=MediaStatus.UPLOADING,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    return asset


def asset_id_placeholder(filename: str) -> str:
    """Keep object names safe and avoid collisions between repeated filenames."""
    from uuid import uuid4

    return f"{uuid4()}-{filename}"


@router.post("/multipart-upload/initiate", response_model=MultipartUploadInitiateResponse, status_code=status.HTTP_201_CREATED)
def initiate_multipart_media_upload(payload: UploadURLRequest, db: Session = Depends(get_db)) -> MultipartUploadInitiateResponse:
    asset = _create_uploading_asset(payload, db)
    upload_id = create_multipart_upload(asset.storage_key, payload.content_type)
    return MultipartUploadInitiateResponse(asset_id=asset.id, storage_key=asset.storage_key, upload_id=upload_id, expires_in=settings.presigned_url_expire_seconds)


@router.post("/multipart-upload/part-url", response_model=MultipartPartURLResponse)
def get_multipart_part_url(payload: MultipartPartURLRequest, db: Session = Depends(get_db)) -> MultipartPartURLResponse:
    asset = db.get(MediaAsset, payload.asset_id)
    if asset is None or asset.status != MediaStatus.UPLOADING:
        raise HTTPException(status_code=409, detail="Media asset is unavailable for multipart upload")
    return MultipartPartURLResponse(upload_url=create_multipart_part_url(asset.storage_key, payload.upload_id, payload.part_number))


def _mark_upload_completed(asset: MediaAsset, db: Session) -> MediaAsset:
    asset.status = MediaStatus.READY if asset.media_type == MediaType.IMAGE else MediaStatus.PROCESSING
    db.commit(); db.refresh(asset)
    if asset.media_type != MediaType.IMAGE:
        process_new_media.delay(str(asset.id))
    return asset


@router.post("/multipart-upload/complete", response_model=MediaAssetResponse)
def complete_multipart_media_upload(payload: MultipartCompleteRequest, db: Session = Depends(get_db)) -> MediaAssetResponse:
    asset = db.get(MediaAsset, payload.asset_id)
    if asset is None or asset.status != MediaStatus.UPLOADING:
        raise HTTPException(status_code=409, detail="Media asset is unavailable for multipart completion")
    parts = [{"PartNumber": item.part_number, "ETag": item.etag} for item in sorted(payload.parts, key=lambda item: item.part_number)]
    complete_multipart_upload(asset.storage_key, payload.upload_id, parts)
    return _mark_upload_completed(asset, db)


@router.post("/confirm-upload", response_model=MediaAssetResponse)
def confirm_upload(
    payload: ConfirmUploadRequest, db: Session = Depends(get_db)
) -> MediaAssetResponse:
    asset = db.get(MediaAsset, payload.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    if asset.status != MediaStatus.UPLOADING:
        raise HTTPException(status_code=409, detail=f"Asset is already {asset.status.value}")
    if not object_exists(asset.storage_key):
        raise HTTPException(status_code=400, detail="Uploaded object was not found in storage")

    return _mark_upload_completed(asset, db)
