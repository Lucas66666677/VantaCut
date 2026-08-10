"""Short-lived, signed capability links for a mobile timeline preview."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import time
from uuid import UUID

from app.core.config import settings


class MobileHandoffTokenError(ValueError):
    pass


def _signature(payload: str) -> str:
    digest = hmac.new(settings.mobile_handoff_token_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_mobile_handoff_token(timeline_id: UUID, *, ttl_seconds: int | None = None) -> tuple[str, int]:
    expires_at = int(time.time()) + (ttl_seconds or settings.mobile_handoff_ttl_seconds)
    payload = f"{timeline_id}.{expires_at}.{secrets.token_urlsafe(18)}"
    return f"{payload}.{_signature(payload)}", expires_at


def verify_mobile_handoff_token(token: str) -> UUID:
    try:
        timeline_raw, expiry_raw, _nonce, signature = token.split(".", 3)
        payload = token.rsplit(".", 1)[0]
        timeline_id, expires_at = UUID(timeline_raw), int(expiry_raw)
    except (ValueError, AttributeError) as exc:
        raise MobileHandoffTokenError("Invalid mobile preview token") from exc
    if expires_at < int(time.time()):
        raise MobileHandoffTokenError("Mobile preview link has expired")
    if not hmac.compare_digest(signature, _signature(payload)):
        raise MobileHandoffTokenError("Invalid mobile preview token")
    return timeline_id


def qr_code_data_uri(url: str) -> str:
    """Return an inline SVG QR image; the dependency is intentionally loaded on demand."""
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError("qrcode is required for mobile preview handoff") from exc
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    output = io.BytesIO()
    image.save(output)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"

