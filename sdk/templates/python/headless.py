"""Ergonomic facade layered over generated OpenAPI models."""
from __future__ import annotations

from typing import Any

import httpx


class VideoAPI:
    def __init__(self, client: "AIEditorClient") -> None: self._client = client
    def rough_cut(self, url: str, *, instructions: dict[str, Any] | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._client._submit("rough-cut", url, instructions or {}, idempotency_key)
    def render(self, url: str, *, instructions: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        return self._client._submit("render", url, instructions, idempotency_key)


class AIEditorClient:
    def __init__(self, api_key: str, base_url: str = "https://api.example.com") -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), headers={"X-API-Key": api_key}, timeout=30)
        self.video = VideoAPI(self)
    def _submit(self, operation: str, url: str, instructions: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        response = self._http.post(f"/api/v1/platform/v1/videos/{operation}", json={"source_url": url, "instructions": instructions}, headers={"Idempotency-Key": idempotency_key or __import__("uuid").uuid4().hex})
        response.raise_for_status(); return response.json()
    def get_job(self, job_id: str) -> dict[str, Any]:
        response = self._http.get(f"/api/v1/platform/v1/jobs/{job_id}"); response.raise_for_status(); return response.json()
