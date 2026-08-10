"""Authentication, atomic Redis throttling and SSRF-safe URL handling for the public API."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from redis import Redis

from app.core.config import settings
from app.models.entities import PlatformAPIKey


class PlatformSecurityError(RuntimeError):
    pass


TOKEN_BUCKET_LUA = """
local now = redis.call('TIME')
local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)
local stored = redis.call('HMGET', KEYS[1], 'tokens', 'updated_ms')
local tokens = tonumber(stored[1]) or tonumber(ARGV[2])
local updated = tonumber(stored[2]) or now_ms
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
tokens = math.min(burst, tokens + math.max(0, now_ms - updated) / 1000 * rate)
if tokens < cost then
  local retry_ms = math.ceil((cost - tokens) / rate * 1000)
  redis.call('HMSET', KEYS[1], 'tokens', tokens, 'updated_ms', now_ms)
  redis.call('PEXPIRE', KEYS[1], math.max(1000, math.ceil(burst / rate * 2000)))
  return {0, tokens, retry_ms}
end
tokens = tokens - cost
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'updated_ms', now_ms)
redis.call('PEXPIRE', KEYS[1], math.max(1000, math.ceil(burst / rate * 2000)))
return {1, tokens, 0}
"""


def _pepper() -> bytes:
    if not settings.platform_api_key_pepper:
        raise PlatformSecurityError("PLATFORM_API_KEY_PEPPER is not configured")
    return settings.platform_api_key_pepper.encode("utf-8")


def hash_api_key(raw_key: str) -> str:
    return hmac.new(_pepper(), raw_key.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_api_key() -> tuple[str, str, str]:
    prefix = f"avpk_{secrets.token_hex(5)}"
    raw = f"{prefix}_{secrets.token_urlsafe(32)}"
    return raw, prefix, hash_api_key(raw)


def _fernet() -> Fernet:
    if not settings.platform_webhook_encryption_key:
        raise PlatformSecurityError("PLATFORM_WEBHOOK_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(settings.platform_webhook_encryption_key.encode("ascii"))
    except Exception as exc:
        raise PlatformSecurityError("PLATFORM_WEBHOOK_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt_webhook_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_webhook_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise PlatformSecurityError("Webhook signing secret cannot be decrypted") from exc


@lru_cache(maxsize=1)
def _redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=False)


def consume_request_token(api_key: PlatformAPIKey, *, cost: float = 1.0) -> tuple[bool, int]:
    if not api_key.is_active:
        raise PlatformSecurityError("API key is disabled")
    redis_key = f"{settings.platform_redis_key_prefix}:{api_key.id}"
    result = _redis().eval(
        TOKEN_BUCKET_LUA, 1, redis_key, float(api_key.rate_limit_rps), int(api_key.burst_limit), cost,
    )
    allowed, _tokens, retry_ms = (int(result[0]), float(result[1]), int(result[2]))
    return bool(allowed), max(1, (retry_ms + 999) // 1000)


def _is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not any((ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_reserved, ip.is_unspecified))


def validate_public_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise PlatformSecurityError("URL must be an absolute http(s) URL without embedded credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise PlatformSecurityError("URL has an invalid port") from exc
    if port not in {None, 80, 443}:
        raise PlatformSecurityError("Only ports 80 and 443 are permitted for platform URLs")
    if settings.platform_allow_private_source_urls:
        return value
    try:
        resolved = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, port or 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise PlatformSecurityError("URL hostname cannot be resolved") from exc
    if not resolved or any(not _is_public_address(address) for address in resolved):
        raise PlatformSecurityError("URL resolves to a private or prohibited network")
    return value


def download_public_video(source_url: str, destination: Path) -> tuple[str, int]:
    """Stream a public video with redirect revalidation and a strict byte ceiling."""
    current = validate_public_url(source_url)
    with httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=False) as client:
        for _ in range(4):
            with client.stream("GET", current, headers={"Accept": "video/*,application/octet-stream"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise PlatformSecurityError("Redirect response has no location")
                    current = validate_public_url(urljoin(current, location))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0].lower()
                if not (content_type.startswith("video/") or content_type == "application/octet-stream"):
                    raise PlatformSecurityError("Source URL did not return a video content type")
                declared = int(response.headers.get("content-length", "0") or 0)
                if declared > settings.platform_max_source_bytes:
                    raise PlatformSecurityError("Source exceeds the platform byte limit")
                written = 0
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        written += len(chunk)
                        if written > settings.platform_max_source_bytes:
                            raise PlatformSecurityError("Source exceeds the platform byte limit")
                        output.write(chunk)
                return content_type, written
    raise PlatformSecurityError("Too many redirects while downloading source")


def mark_key_used(api_key: PlatformAPIKey) -> None:
    api_key.last_used_at = datetime.now(UTC)
