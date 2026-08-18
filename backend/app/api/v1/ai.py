from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers.base import MultimodalProvider
from app.ai.providers.factory import get_vision_provider
from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import MediaAsset, User

router = APIRouter(prefix="/ai", tags=["ai"])


def vision_provider_dependency() -> MultimodalProvider:
    return get_vision_provider()


def _resolve_owned_media_asset(db: Session, video_uri: str, current_user: User) -> MediaAsset:
    """Resolve `video_uri` to a MediaAsset the caller owns, or raise 404.

    `video_uri` is this codebase's established convention for
    `MediaAsset.storage_key` — every other internal caller of
    `MultimodalProvider.analyze_video` passes exactly that value (e.g.
    `app/tasks/social_tasks.py`: `generate_metadata(video_uri=asset.storage_key,
    ...)`, and identically in app/services/final_cut.py,
    app/services/bgm_recommender.py, app/services/soundscape.py,
    app/services/template_extraction.py, app/tasks/embedding_tasks.py,
    app/tasks/lecturas_tasks.py, app/tasks/audio_description_tasks.py). This
    endpoint is the only caller of analyze_video that takes video_uri directly
    from an external, unauthenticated-by-default HTTP request rather than from
    an already-authorized internal pipeline, so — unlike those callers — it
    must verify the resolved asset belongs to the caller before spending a
    paid provider call on it.
    """
    media_asset = db.execute(
        select(MediaAsset).where(MediaAsset.storage_key == video_uri)
    ).scalar_one_or_none()
    if media_asset is None or media_asset.project.owner_id != current_user.id:
        # Same response for "no such media" and "exists but isn't yours" — do
        # not confirm the existence of another user's media to an
        # unauthorized caller.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    return media_asset


@router.post("/analyze-video")
def analyze_video(
    video_uri: str,
    prompt: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: MultimodalProvider = Depends(vision_provider_dependency),
) -> dict[str, Any]:
    # Ownership is verified BEFORE `provider.analyze_video` is ever called, so
    # an unauthorized (anonymous or non-owner) request never reaches the paid
    # AI provider. See _resolve_owned_media_asset for why video_uri ==
    # MediaAsset.storage_key is a safe, evidenced assumption rather than a
    # fabricated one.
    #
    # No AI-specific rate limit/quota mechanism exists in this codebase today
    # that cleanly applies here — `User.render_credits` gates render jobs
    # specifically (app/api/v1/renders.py), and PlatformAPIKey.rate_limit_rps
    # (app/services/platform_security.py) belongs to the separate
    # platform-API-key auth scheme, not regular user sessions. Building a new
    # AI usage quota is out of scope for this batch; recorded as a follow-up
    # in vantacut-batch1-checkpoint.md rather than invented here.
    _resolve_owned_media_asset(db, video_uri, current_user)
    return provider.analyze_video(video_uri, prompt)
