from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from app.ai.providers.factory import get_vision_provider
from app.ai.publishing_prompts import (
    PUBLISHING_METADATA_SYSTEM_PROMPT,
    PUBLISHING_METADATA_USER_PROMPT,
    publishing_metadata_schema,
)
from app.schemas.social import GeneratedSocialMetadata
from app.services.bgm_recommender import extract_bgm_frames, uniformly_sample_kept_timeline
from app.schemas.subtitle import ConfirmedTimelineSegment


class PublishingMetadataError(RuntimeError):
    pass


def _keep_segments(document: dict[str, Any]) -> list[ConfirmedTimelineSegment]:
    raw = document.get("segments", [])
    if not raw:
        raw = [clip for track in document.get("tracks", []) if track.get("type") == "main_video" for clip in track.get("clips", [])]
    segments = [ConfirmedTimelineSegment.model_validate(item) for item in raw]
    kept = [segment for segment in segments if segment.action == "keep"]
    if not kept:
        raise PublishingMetadataError("Confirmed timeline has no kept segments")
    return kept


def transcript_from_timeline_settings(settings_json: dict[str, Any]) -> str:
    subtitles = dict(settings_json.get("subtitles", {}))
    text = " ".join(str(item.get("text", "")) for item in subtitles.get("items", []) if isinstance(item, dict))
    if not text.strip():
        text = str(dict(settings_json.get("confirmed_timeline", {})).get("transcript", ""))
    return text.strip() or "No transcript available; use only the visual samples and do not invent spoken claims."


def generate_metadata(*, video_uri: str, video_path: Path, settings_json: dict[str, Any]) -> GeneratedSocialMetadata:
    document = dict(settings_json.get("confirmed_timeline", {}))
    segments = _keep_segments(document)
    with tempfile.TemporaryDirectory(prefix="publishing-metadata-") as temp_dir:
        frames = extract_bgm_frames(video_path, uniformly_sample_kept_timeline(segments, count=5), Path(temp_dir) / "frames")
        result = get_vision_provider().analyze_video(
            video_uri,
            f"{PUBLISHING_METADATA_SYSTEM_PROMPT}\n\n{PUBLISHING_METADATA_USER_PROMPT}",
            response_schema=publishing_metadata_schema(),
            context={"task": "publishing_metadata", "transcript": transcript_from_timeline_settings(settings_json), "sampled_frames": frames, "timeline_duration": sum(item.source_end - item.source_start for item in segments)},
        )
    try:
        metadata = GeneratedSocialMetadata.model_validate(result)
    except Exception as exc:
        raise PublishingMetadataError("Multimodal provider returned invalid publishing metadata JSON") from exc
    if metadata.chapters[0].start_time != 0:
        metadata.chapters.insert(0, {"start_time": 0, "title": "Introduction"})
    metadata.chapters.sort(key=lambda chapter: chapter.start_time)
    return metadata
