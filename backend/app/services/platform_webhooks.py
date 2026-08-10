"""Signed at-least-once webhook delivery for headless platform jobs."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.core.config import settings
from app.services.platform_security import decrypt_webhook_secret, validate_public_url


class WebhookDeliveryError(RuntimeError):
    pass


def webhook_payload(job: Any) -> dict[str, Any]:
    return {
        "id": str(job.id), "operation": job.operation, "status": job.status,
        "result": job.result_json or {}, "error": job.error_message,
        "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat(),
    }


def send_signed_webhook(*, url: str, encrypted_secret: str, event: str, delivery_id: str, payload: dict[str, Any]) -> int:
    safe_url = validate_public_url(url)
    timestamp = str(int(time.time()))
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    secret = decrypt_webhook_secret(encrypted_secret).encode("utf-8")
    signature = hmac.new(secret, b".".join((timestamp.encode("ascii"), body)), hashlib.sha256).hexdigest()
    try:
        response = httpx.post(
            safe_url, content=body,
            headers={
                "Content-Type": "application/json", "User-Agent": "AI-Video-Platform-Webhooks/1.0",
                "X-AIVideo-Event": event, "X-AIVideo-Delivery": delivery_id,
                "X-AIVideo-Signature": f"t={timestamp},v1={signature}",
            }, timeout=settings.platform_webhook_timeout_seconds, follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise WebhookDeliveryError(f"Webhook network error: {exc}") from exc
    if not 200 <= response.status_code < 300:
        raise WebhookDeliveryError(f"Webhook endpoint returned HTTP {response.status_code}")
    return response.status_code
