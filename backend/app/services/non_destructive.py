"""Guardrails for keeping uploaded originals immutable and edits declarative."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


class OriginalMediaProtectionError(PermissionError):
    pass


def assert_not_original_overwrite(storage_key: str) -> None:
    """Workers may only create derived artifacts; browser presigned upload owns originals."""
    normalized = storage_key.replace("\\", "/")
    if "/original/" in f"/{normalized.lstrip('/')}":
        raise OriginalMediaProtectionError("AI workers cannot overwrite an uploaded original object")


def append_filter_layer(settings: dict[str, Any], *, kind: str, target: dict[str, Any], parameters: dict[str, Any], source: str = "ai") -> dict[str, Any]:
    """Append an immutable render instruction; never replace bytes belonging to MediaAsset.storage_key."""
    updated = deepcopy(settings)
    sandbox = dict(updated.get("non_destructive_sandbox") or {})
    layers = list(sandbox.get("filter_layers") or [])
    layers.append({"id": f"{kind}-{len(layers) + 1}", "kind": kind, "target": target, "parameters": parameters, "source": source, "enabled": True})
    sandbox.update({"schema": "com.aivideo.non-destructive.v1", "original_media_immutable": True, "filter_layers": layers})
    updated["non_destructive_sandbox"] = sandbox
    return updated
