"""OAuth and publish adapters. Tokens never leave this module in plaintext."""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.models.entities import SocialPlatform
from app.services.storage import create_download_url


class SocialPlatformError(RuntimeError):
    pass


class TokenCipher:
    def __init__(self) -> None:
        key = settings.social_token_encryption_key
        if not key:
            raise SocialPlatformError("SOCIAL_TOKEN_ENCRYPTION_KEY is required to connect social accounts")
        try:
            self._fernet = Fernet(key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise SocialPlatformError("SOCIAL_TOKEN_ENCRYPTION_KEY must be a urlsafe base64 Fernet key") from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SocialPlatformError("Stored social token cannot be decrypted") from exc


def make_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class SocialPlatformClient(ABC):
    platform: SocialPlatform

    @abstractmethod
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    @abstractmethod
    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> dict[str, Any]: ...

    @abstractmethod
    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]: ...

    @abstractmethod
    def account_profile(self, access_token: str) -> dict[str, Any]: ...

    @abstractmethod
    def publish_video(self, *, access_token: str, video_path: Path, title: str, description: str, visibility: str, source_key: str) -> dict[str, Any]: ...


class YouTubeClient(SocialPlatformClient):
    platform = SocialPlatform.YOUTUBE
    scopes = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly", "https://www.googleapis.com/auth/yt-analytics.readonly"]

    def _credentials(self) -> tuple[str, str]:
        if not settings.youtube_client_id or not settings.youtube_client_secret:
            raise SocialPlatformError("YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET are required")
        return settings.youtube_client_id, settings.youtube_client_secret

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        client_id, _ = self._credentials()
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(self.scopes), "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": code_challenge, "code_challenge_method": "S256"})

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> dict[str, Any]:
        client_id, client_secret = self._credentials()
        response = httpx.post("https://oauth2.googleapis.com/token", data={"code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": code_verifier}, timeout=30)
        response.raise_for_status()
        return response.json()

    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        client_id, client_secret = self._credentials()
        response = httpx.post("https://oauth2.googleapis.com/token", data={"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token, "grant_type": "refresh_token"}, timeout=30)
        response.raise_for_status()
        return response.json()

    def account_profile(self, access_token: str) -> dict[str, Any]:
        response = httpx.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "id,snippet", "mine": "true"}, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
        response.raise_for_status()
        item = response.json().get("items", [{}])[0]
        return {"id": item.get("id"), "display_name": item.get("snippet", {}).get("title"), "raw": item}

    def publish_video(self, *, access_token: str, video_path: Path, title: str, description: str, visibility: str, source_key: str) -> dict[str, Any]:
        metadata = {"snippet": {"title": title[:100], "description": description}, "status": {"privacyStatus": visibility if visibility in {"public", "unlisted", "private"} else "private"}}
        init = httpx.post("https://www.googleapis.com/upload/youtube/v3/videos", params={"part": "snippet,status", "uploadType": "resumable"}, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(video_path.stat().st_size)}, json=metadata, timeout=60)
        init.raise_for_status()
        upload_url = init.headers["Location"]
        with video_path.open("rb") as stream:
            uploaded = httpx.put(upload_url, headers={"Content-Type": "video/mp4", "Content-Length": str(video_path.stat().st_size)}, content=stream.read(), timeout=60 * 30)
        uploaded.raise_for_status()
        return {"platform_post_id": uploaded.json()["id"], "raw": uploaded.json()}

    def set_thumbnail(self, *, access_token: str, video_id: str, image_path: Path) -> None:
        with image_path.open("rb") as stream:
            response = httpx.post("https://www.googleapis.com/upload/youtube/v3/thumbnails/set", params={"videoId": video_id, "uploadType": "media"}, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "image/jpeg"}, content=stream.read(), timeout=90)
        response.raise_for_status()

    def read_reach_metrics(self, *, access_token: str, video_id: str) -> dict[str, Any]:
        # Data is subject to YouTube Analytics/reporting latency. This snapshot is never treated as real-time attribution.
        today = datetime.now(UTC).date().isoformat()
        response = httpx.get("https://youtubeanalytics.googleapis.com/v2/reports", params={"ids": "channel==MINE", "startDate": today, "endDate": today, "metrics": "videoThumbnailImpressions,videoThumbnailImpressionsClickRate", "filters": f"video=={video_id}"}, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
        if response.status_code >= 400:
            return {"impressions": None, "ctr": None, "data_available": False, "error": response.text[-500:]}
        rows = response.json().get("rows", [])
        if not rows:
            return {"impressions": None, "ctr": None, "data_available": False, "raw": response.json()}
        return {"impressions": int(rows[0][0]), "ctr": float(rows[0][1]), "data_available": True, "raw": response.json()}


class TikTokClient(SocialPlatformClient):
    platform = SocialPlatform.TIKTOK
    scopes = ["user.info.basic", "video.upload", "video.publish"]

    def _credentials(self) -> tuple[str, str]:
        if not settings.tiktok_client_key or not settings.tiktok_client_secret:
            raise SocialPlatformError("TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET are required")
        return settings.tiktok_client_key, settings.tiktok_client_secret

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        client_key, _ = self._credentials()
        return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({"client_key": client_key, "response_type": "code", "scope": ",".join(self.scopes), "redirect_uri": redirect_uri, "state": state, "code_challenge": code_challenge, "code_challenge_method": "S256"})

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> dict[str, Any]:
        client_key, client_secret = self._credentials()
        response = httpx.post("https://open.tiktokapis.com/v2/oauth/token/", data={"client_key": client_key, "client_secret": client_secret, "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri, "code_verifier": code_verifier}, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        response.raise_for_status()
        return response.json()

    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        client_key, client_secret = self._credentials()
        response = httpx.post("https://open.tiktokapis.com/v2/oauth/token/", data={"client_key": client_key, "client_secret": client_secret, "grant_type": "refresh_token", "refresh_token": refresh_token}, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        response.raise_for_status()
        return response.json()

    def account_profile(self, access_token: str) -> dict[str, Any]:
        response = httpx.get("https://open.tiktokapis.com/v2/user/info/", params={"fields": "open_id,display_name,avatar_url"}, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
        response.raise_for_status()
        user = response.json().get("data", {}).get("user", {})
        return {"id": user.get("open_id"), "display_name": user.get("display_name"), "raw": user}

    def publish_video(self, *, access_token: str, video_path: Path, title: str, description: str, visibility: str, source_key: str) -> dict[str, Any]:
        # Direct Post requires TikTok app approval and explicit creator-facing controls. PULL_FROM_URL needs a verified domain.
        payload = {"post_info": {"title": f"{title}\n{description}"[:2200], "privacy_level": "SELF_ONLY" if visibility == "self_only" else "PUBLIC_TO_EVERYONE", "disable_duet": False, "disable_stitch": False, "disable_comment": False}, "source_info": {"source": "PULL_FROM_URL", "video_url": create_download_url(source_key, expires_in=3600)}}
        response = httpx.post("https://open.tiktokapis.com/v2/post/publish/video/init/", headers={"Authorization": f"Bearer {access_token}"}, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json().get("data", {})
        return {"platform_post_id": data.get("publish_id"), "awaiting_creator": False, "raw": response.json()}


def get_social_client(platform: SocialPlatform) -> SocialPlatformClient:
    if platform == SocialPlatform.YOUTUBE:
        return YouTubeClient()
    if platform == SocialPlatform.TIKTOK:
        return TikTokClient()
    raise SocialPlatformError(f"Unsupported platform: {platform.value}")


def token_expiry(token_response: dict[str, Any]) -> datetime | None:
    expires_in = token_response.get("expires_in")
    return datetime.now(UTC) + timedelta(seconds=int(expires_in)) if expires_in else None


def access_token_for_account(account: Any) -> str:
    """Return a usable token and refresh the mapped account before the caller commits it."""
    cipher = TokenCipher()
    if not account.token_expires_at or account.token_expires_at > datetime.now(UTC) + timedelta(minutes=2):
        return cipher.decrypt(account.encrypted_access_token)
    if not account.encrypted_refresh_token:
        raise SocialPlatformError("Social account access token expired; reconnect this account")
    refreshed = get_social_client(account.platform).refresh_access_token(cipher.decrypt(account.encrypted_refresh_token))
    account.encrypted_access_token = cipher.encrypt(refreshed["access_token"])
    if refreshed.get("refresh_token"):
        account.encrypted_refresh_token = cipher.encrypt(refreshed["refresh_token"])
    account.token_expires_at = token_expiry(refreshed)
    return cipher.decrypt(account.encrypted_access_token)
