"""The first timeline for an uploaded asset: one keep segment, whole clip.

## Why this exists

Every export route is keyed by a timeline id. `POST /timelines/{id}/render`,
the omnichannel matrix, the subtitle burner, the download URL — all of them
start from a timeline that belongs to a project the caller owns. Nothing in
`app/api/v1` created one. Every `Timeline(...)` in the backend is inside a
Celery task or an AI pipeline: the auto-director, one-click templates, beat
sync, auto-narration, long-to-shorts, live camera ingest, or a clone of a
timeline that already exists. A person who uploaded a video had no way to
obtain the id every later call needs.

The document below is what makes the timeline *renderable* rather than merely
present. `_render_duration` in `app/api/v1/renders.py` sums the keep segments
and rejects a render with `400 Confirmed timeline has no keep segments` when
the total is zero, so an empty timeline would have moved the failure one step
later without unblocking anything.

## The shape

`confirmed_timeline` here matches what `app/api/v1/subtitles.py` already
writes — `source_asset_id` plus flat `segments` — rather than the richer
`tracks` form the auto-director emits. `_render_duration` reads `tracks` when
present and falls back to `segments`, and `_render_asset_ids` reads
`source_asset_id`, so the flat form is the smallest document both accept. An
editor that later adds tracks replaces it; nothing here has to know that.

Kept as a pure function, separate from the route, so the arithmetic and the
refusals can be exercised without a database — which is what makes them
testable on a machine that has no PostgreSQL.
"""

from __future__ import annotations

import math
from typing import Any
from uuid import UUID


class TimelineBootstrapError(ValueError):
    """The asset cannot be turned into something renderable, and why."""


def build_initial_confirmed_timeline(
    *, source_asset_id: UUID | str, duration_seconds: float | int | None
) -> dict[str, Any]:
    """One keep segment covering the whole asset.

    A duration is required rather than defaulted. Guessing one would produce a
    timeline that renders the wrong length — a plausible-looking artifact that
    is silently wrong, which is worse than a refusal the caller can act on.
    `duration_seconds` is `NULL` until `process_new_media` has probed the file,
    so the honest answer while that is pending is "not yet".
    """

    if duration_seconds is None:
        raise TimelineBootstrapError(
            "Asset duration is unknown, so a full-length segment cannot be built. "
            "Wait for media processing to finish before creating a timeline."
        )
    duration = float(duration_seconds)
    if not math.isfinite(duration):
        raise TimelineBootstrapError("Asset duration is not a finite number of seconds.")
    if duration <= 0:
        raise TimelineBootstrapError(
            f"Asset duration must be greater than zero seconds, got {duration}."
        )
    return {
        "source_asset_id": str(source_asset_id),
        "segments": [{"source_start": 0.0, "source_end": duration, "action": "keep"}],
    }


__all__ = ["TimelineBootstrapError", "build_initial_confirmed_timeline"]
