from __future__ import annotations

import json
import secrets
from datetime import datetime
from uuid import UUID

import redis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.entities import RenderJob, SocialAccount, SocialPlatform, SocialPost, SocialPostStatus, Timeline, User
from app.schemas.social import (
    MetadataGenerationResponse,
    OAuthAuthorizationResponse,
    PublishTimelineRequest,
    PublishTimelineResponse,
    SocialAccountResponse,
)
from app.services.social_platforms import TokenCipher, get_social_client, make_pkce_pair, token_expiry
from app.tasks.social_tasks import generate_metadata_for_timeline, publish_timeline


router = APIRouter(prefix="/social", tags=["social-publishing"])
STATE_TTL_SECONDS = 10 * 60


def _platform(value: str) -> SocialPlatform:
    try:
        return SocialPlatform(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Unsupported social platform") from exc


def _redirect_uri(platform: SocialPlatform) -> str:
    return f"{settings.social_oauth_redirect_base_url.rstrip('/')}/api/v1/social/oauth/{platform.value}/callback"


@router.get("/oauth/{platform}/authorize", response_model=OAuthAuthorizationResponse)
def begin_oauth(platform: str, user_id: UUID, db: Session = Depends(get_db)) -> OAuthAuthorizationResponse:
    chosen = _platform(platform)
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    state, (verifier, challenge) = secrets.token_urlsafe(32), make_pkce_pair()
    redis.from_url(settings.redis_url, decode_responses=True).setex(f"social-oauth:{state}", STATE_TTL_SECONDS, json.dumps({"user_id": str(user_id), "platform": chosen.value, "verifier": verifier}))
    return OAuthAuthorizationResponse(platform=chosen.value, authorization_url=get_social_client(chosen).authorization_url(state=state, code_challenge=challenge, redirect_uri=_redirect_uri(chosen)))


@router.get("/oauth/{platform}/callback")
def complete_oauth(platform: str, code: str, state: str, db: Session = Depends(get_db)) -> JSONResponse:
    chosen = _platform(platform)
    store = redis.from_url(settings.redis_url, decode_responses=True)
    raw_state = store.getdel(f"social-oauth:{state}")
    if not raw_state:
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired")
    saved = json.loads(raw_state)
    if saved["platform"] != chosen.value:
        raise HTTPException(status_code=400, detail="OAuth state platform mismatch")
    client = get_social_client(chosen)
    token_response = client.exchange_code(code=code, code_verifier=saved["verifier"], redirect_uri=_redirect_uri(chosen))
    access_token = token_response["access_token"]
    profile = client.account_profile(access_token)
    account_id = profile.get("id")
    if not account_id:
        raise HTTPException(status_code=502, detail="Platform did not return an account id")
    account = db.scalar(select(SocialAccount).where(SocialAccount.user_id == UUID(saved["user_id"]), SocialAccount.platform == chosen, SocialAccount.platform_account_id == account_id))
    cipher = TokenCipher()
    if account is None:
        account = SocialAccount(user_id=UUID(saved["user_id"]), platform=chosen, platform_account_id=account_id, encrypted_access_token=cipher.encrypt(access_token))
        db.add(account)
    account.display_name = profile.get("display_name")
    account.encrypted_access_token = cipher.encrypt(access_token)
    account.encrypted_refresh_token = cipher.encrypt(token_response["refresh_token"]) if token_response.get("refresh_token") else account.encrypted_refresh_token
    account.token_expires_at = token_expiry(token_response)
    account.scopes_json = token_response.get("scope", "").replace(",", " ").split()
    account.profile_json = profile.get("raw", {})
    db.commit()
    return JSONResponse({"connected": True, "platform": chosen.value, "social_account_id": str(account.id)})


@router.get("/accounts", response_model=list[SocialAccountResponse])
def list_accounts(user_id: UUID, db: Session = Depends(get_db)) -> list[SocialAccountResponse]:
    accounts = db.scalars(select(SocialAccount).where(SocialAccount.user_id == user_id).order_by(SocialAccount.created_at.desc())).all()
    return [SocialAccountResponse(id=item.id, platform=item.platform.value, platform_account_id=item.platform_account_id, display_name=item.display_name, scopes=item.scopes_json, token_expires_at=item.token_expires_at) for item in accounts]


@router.post("/timelines/{timeline_id}/metadata", response_model=MetadataGenerationResponse, status_code=status.HTTP_202_ACCEPTED)
def request_metadata(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> MetadataGenerationResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if timeline.project.owner_id != user_id:
        raise HTTPException(status_code=403, detail="User cannot generate metadata for this timeline")
    task = generate_metadata_for_timeline.delay(str(timeline.id))
    return MetadataGenerationResponse(timeline_id=timeline.id, task_id=task.id)


@router.post("/timelines/{timeline_id}/publish", response_model=PublishTimelineResponse, status_code=status.HTTP_202_ACCEPTED)
def request_publish(timeline_id: UUID, payload: PublishTimelineRequest, db: Session = Depends(get_db)) -> PublishTimelineResponse:
    timeline, account, render = db.get(Timeline, timeline_id), db.get(SocialAccount, payload.social_account_id), db.get(RenderJob, payload.render_job_id)
    if not timeline or not account or not render:
        raise HTTPException(status_code=404, detail="Timeline, social account, or render job not found")
    if timeline.project.owner_id != payload.user_id or account.user_id != payload.user_id or render.timeline_id != timeline.id:
        raise HTTPException(status_code=403, detail="Publishing resources do not belong to this user and timeline")
    if render.status.value != "completed":
        raise HTTPException(status_code=409, detail="Render job is not completed")
    if payload.start_thumbnail_experiment and (account.platform != SocialPlatform.YOUTUBE or len(payload.thumbnail_candidate_keys) not in {0, 3}):
        raise HTTPException(status_code=422, detail="YouTube thumbnail experiment needs zero (auto-generate) or exactly three candidate object keys")
    post = SocialPost(social_account_id=account.id, timeline_id=timeline.id, render_job_id=render.id, status=SocialPostStatus.QUEUED, metadata_json={"title": payload.title, "description": payload.description, "visibility": payload.visibility, "thumbnail_candidate_keys": payload.thumbnail_candidate_keys if payload.start_thumbnail_experiment else [], "generate_thumbnail_candidates": payload.start_thumbnail_experiment and not payload.thumbnail_candidate_keys})
    db.add(post)
    db.commit()
    task = publish_timeline.delay(str(post.id))
    return PublishTimelineResponse(social_post_id=post.id, task_id=task.id, status=post.status.value)
