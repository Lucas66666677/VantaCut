"""The arithmetic that decides whether an uploaded asset can be exported.

`build_initial_confirmed_timeline` is the whole reason the new route unblocks
anything. A timeline without a keep segment is accepted by the database and
then refused by `POST /timelines/{id}/render` with
`400 Confirmed timeline has no keep segments`, so "a timeline exists" and "an
export can be requested" are different claims and only the second one matters.

These are pure: no database, no storage, no queue. That is deliberate — it
means the refusals can be exercised on a machine with no PostgreSQL, while the
ownership and status checks around them run against the real schema in
`test_project_timeline_provisioning.py`.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.timeline_bootstrap import (
    TimelineBootstrapError,
    build_initial_confirmed_timeline,
)


def test_the_document_covers_the_whole_asset() -> None:
    asset_id = uuid.uuid4()

    document = build_initial_confirmed_timeline(source_asset_id=asset_id, duration_seconds=12.5)

    assert document["source_asset_id"] == str(asset_id)
    assert document["segments"] == [
        {"source_start": 0.0, "source_end": 12.5, "action": "keep"}
    ]


def test_the_render_route_reads_a_positive_duration_from_it() -> None:
    """The check that makes this more than a shape assertion.

    `_render_duration` is what stands between an uploaded asset and an export,
    so its arithmetic is reproduced here against the document actually
    produced. Asserting only the JSON shape would pass just as happily for a
    document the renderer scores at zero.
    """
    document = build_initial_confirmed_timeline(
        source_asset_id=uuid.uuid4(), duration_seconds=8.25
    )

    # Copied from `app/api/v1/renders.py::_render_duration`: with no "tracks"
    # key it falls through to the flat "segments" list.
    tracks = document.get("tracks", [])
    segments = (
        [clip for track in tracks if track.get("type") == "main_video" for clip in track.get("clips", [])]
        if tracks
        else document.get("segments", [])
    )
    duration = sum(
        float(segment["source_end"]) - float(segment["source_start"])
        for segment in segments
        if segment.get("action", "keep") == "keep"
    )

    assert duration == pytest.approx(8.25)
    assert duration > 0, "a zero-length timeline is refused by the render route"


def test_the_source_asset_is_discoverable_for_cold_storage_hydration() -> None:
    """`_render_asset_ids` reads `source_asset_id`; a 1080p render needs it.

    Without this key a high-quality render would skip the archive check and
    hand FFmpeg an object that may still be in Deep Archive.
    """
    asset_id = uuid.uuid4()
    document = build_initial_confirmed_timeline(source_asset_id=asset_id, duration_seconds=3.0)

    raw_ids = set()
    source_asset_id = document.get("source_asset_id")
    if source_asset_id:
        raw_ids.add(uuid.UUID(str(source_asset_id)))

    assert raw_ids == {asset_id}


def test_an_unprobed_asset_is_refused_rather_than_guessed() -> None:
    """`duration_seconds` is NULL until `process_new_media` has run.

    Defaulting it would produce a timeline that renders the wrong length: an
    artifact that looks fine and is silently wrong, which is worse than a
    refusal naming what to wait for.
    """
    with pytest.raises(TimelineBootstrapError, match="duration is unknown"):
        build_initial_confirmed_timeline(source_asset_id=uuid.uuid4(), duration_seconds=None)


@pytest.mark.parametrize("duration", [0, 0.0, -1.5])
def test_a_non_positive_duration_is_refused(duration: float) -> None:
    """Zero would build a segment the render route scores at zero and rejects,
    one step later and with a message about keep segments rather than about the
    asset."""
    with pytest.raises(TimelineBootstrapError, match="greater than zero"):
        build_initial_confirmed_timeline(source_asset_id=uuid.uuid4(), duration_seconds=duration)


@pytest.mark.parametrize("duration", [float("nan"), float("inf")])
def test_a_non_finite_duration_is_refused(duration: float) -> None:
    """A probe can emit these for a corrupt file. `float("nan") > 0` is False
    and `float("inf") > 0` is True, so neither is caught by the positivity
    check alone -- NaN would fall through to the wrong message and infinity
    would serialise as invalid JSON.
    """
    with pytest.raises(TimelineBootstrapError, match="finite"):
        build_initial_confirmed_timeline(source_asset_id=uuid.uuid4(), duration_seconds=duration)


def test_an_integer_duration_is_accepted() -> None:
    """Guards the guard: the refusals above are a filter, not a wall."""
    document = build_initial_confirmed_timeline(source_asset_id=uuid.uuid4(), duration_seconds=30)

    assert document["segments"][0]["source_end"] == 30.0
