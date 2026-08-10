"""Application-layer authentication for camera-to-cloud chunk uploads."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from redis import Redis

from app.core.config import settings
from app.schemas.camera_ingest import CameraMetadata


class CameraIngestSecurityError(RuntimeError):
    pass


def _fernet() -> Fernet:
    if not settings.ingest_device_encryption_key:
        raise CameraIngestSecurityError("INGEST_DEVICE_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(settings.ingest_device_encryption_key.encode("ascii"))
    except Exception as exc:
        raise CameraIngestSecurityError("INGEST_DEVICE_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt_device_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_device_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CameraIngestSecurityError("Camera device secret cannot be decrypted") from exc


@lru_cache(maxsize=1)
def _redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def decode_camera_metadata(encoded: str | None) -> tuple[CameraMetadata, str]:
    if not encoded:
        metadata = CameraMetadata()
    else:
        if len(encoded) > 24_000:
            raise CameraIngestSecurityError("Camera metadata header is too large")
        try:
            padded = encoded + "=" * (-len(encoded) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            metadata = CameraMetadata.model_validate_json(raw)
        except Exception as exc:
            raise CameraIngestSecurityError("X-Camera-Metadata is not valid URL-safe base64 JSON") from exc
    canonical = json.dumps(metadata.model_dump(mode="json", exclude_none=True), sort_keys=True, separators=(",", ":"))
    return metadata, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_chunk_payload(*, path: str, session_id: str, sequence_number: int, timestamp: str, nonce: str, content_sha256: str, metadata_hash: str) -> bytes:
    return "\n".join(("POST", path, session_id, str(sequence_number), timestamp, nonce, content_sha256.lower(), metadata_hash)).encode("utf-8")


def verify_chunk_signature(*, device_secret: str, device_id: str, path: str, session_id: str, sequence_number: int, timestamp: str | None, nonce: str | None, content_sha256: str | None, signature: str | None, metadata_hash: str) -> None:
    if not timestamp or not nonce or not content_sha256 or not signature:
        raise CameraIngestSecurityError("Missing required signed ingest headers")
    if len(nonce) < 16 or len(nonce) > 200:
        raise CameraIngestSecurityError("Invalid ingest nonce")
    if len(content_sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in content_sha256):
        raise CameraIngestSecurityError("X-Chunk-SHA256 must be a SHA-256 hex digest")
    try:
        signed_at = int(timestamp)
    except ValueError as exc:
        raise CameraIngestSecurityError("X-Ingest-Timestamp must be UNIX seconds") from exc
    if abs(int(time.time()) - signed_at) > settings.ingest_signature_max_age_seconds:
        raise CameraIngestSecurityError("Signed ingest request has expired")

    expected = hmac.new(
        device_secret.encode("utf-8"),
        canonical_chunk_payload(path=path, session_id=session_id, sequence_number=sequence_number, timestamp=timestamp, nonce=nonce, content_sha256=content_sha256, metadata_hash=metadata_hash),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        raise CameraIngestSecurityError("Invalid ingest HMAC signature")
    try:
        accepted = _redis().set(f"ingest:nonce:{device_id}:{nonce}", "1", nx=True, ex=settings.ingest_replay_ttl_seconds)
    except Exception as exc:
        raise CameraIngestSecurityError("Replay protection is unavailable; refusing ingest") from exc
    if not accepted:
        raise CameraIngestSecurityError("Replay detected for ingest nonce")


def metadata_search_text(metadata: dict[str, object]) -> str:
    """Text indexed in pgvector; exact raw metadata stays on the asset/chunk record."""
    parts = ["camera capture"]
    for label, key in (("camera", "camera_model"), ("lens", "lens_model"), ("timecode", "timecode"), ("focal length", "focal_length_mm"), ("aperture", "aperture_f_number"), ("ISO", "iso")):
        value = metadata.get(key)
        if value is not None:
            parts.append(f"{label} {value}")
    latitude, longitude = metadata.get("gps_latitude"), metadata.get("gps_longitude")
    if isinstance(latitude, (float, int)) and isinstance(longitude, (float, int)):
        parts.append(f"GPS {float(latitude):.4f}, {float(longitude):.4f}")
    return "; ".join(parts)
