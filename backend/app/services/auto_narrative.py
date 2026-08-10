"""Frame sampling, multimodal understanding, and safe timeline planning for Auto-Narrative."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.ai.auto_narrative_prompts import (
    AUTO_NARRATIVE_SCRIPT_SYSTEM, AUTO_NARRATIVE_VISION_SYSTEM, narrative_response_schema, vision_response_schema,
)
from app.ai.providers.base import MultimodalProvider, TextAnalysisProvider
from app.schemas.auto_narrative import NarrativePlan, VisualUnderstanding

if TYPE_CHECKING:
    from app.models.entities import MediaAsset


class AutoNarrativeError(ValueError):
    pass


def extract_sampled_frames(video_path: Path, output_dir: Path, *, duration: float, count: int = 4) -> list[Path]:
    """Extract evenly distributed JPEGs. Paths are passed to a multimodal adapter, never persisted."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if duration <= 0:
        raise AutoNarrativeError("A source video must have a positive duration")
    frames: list[Path] = []
    for index in range(max(1, count)):
        timestamp = min(max(.03, duration * (index + .5) / count), max(.03, duration - .03))
        target = output_dir / f"frame-{index:02d}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "3", str(target)],
                check=True, capture_output=True, text=True, timeout=45,
            )
        except subprocess.TimeoutExpired as exc:
            raise AutoNarrativeError("Frame sampling timed out") from exc
        except subprocess.CalledProcessError as exc:
            raise AutoNarrativeError((exc.stderr or "Unable to sample source frames")[-1200:]) from exc
        if target.exists():
            frames.append(target)
    if not frames:
        raise AutoNarrativeError("No decodable frames were extracted from the source video")
    return frames


def understand_asset(
    provider: MultimodalProvider, *, asset: "MediaAsset", local_proxy: Path, frame_paths: list[Path],
) -> VisualUnderstanding:
    duration = float(asset.duration_seconds or 0)
    prompt = (
        f"ASSET_ID: {asset.id}\nSOURCE_DURATION_SECONDS: {duration:.3f}\n"
        "The attached frame paths are chronological samples. Identify the visible activity and choose a compelling interval."
    )
    result = provider.analyze_video(
        str(local_proxy), prompt, response_schema=vision_response_schema(),
        context={
            "task": "auto_narrative_visual_understanding", "asset_id": str(asset.id), "duration_seconds": duration,
            "frame_paths": [str(path) for path in frame_paths], "frame_mime_type": "image/jpeg",
        },
    )
    try:
        understanding = VisualUnderstanding.model_validate({**result, "asset_id": str(result.get("asset_id") or asset.id)})
    except (TypeError, ValueError) as exc:
        raise AutoNarrativeError("Vision provider returned an invalid Auto-Narrative description") from exc
    if understanding.asset_id != str(asset.id):
        raise AutoNarrativeError("Vision provider returned a description for the wrong media asset")
    end = min(duration, understanding.best_source_end)
    start = min(max(0.0, understanding.best_source_start), max(0.0, end - .2))
    if end - start < .2:
        start, end = 0.0, min(duration, max(.2, duration))
    return understanding.model_copy(update={"best_source_start": round(start, 3), "best_source_end": round(end, 3)})


def plan_narrative(
    provider: TextAnalysisProvider, *, understandings: list[VisualUnderstanding], tone: str, language: str, target_duration_seconds: int,
) -> NarrativePlan:
    payload = [item.model_dump(mode="json") for item in understandings]
    user_prompt = (
        "AUTO_NARRATIVE_SCRIPT\n"
        f"TONE: {tone}\nLANGUAGE: {language}\nTARGET_DURATION_SECONDS: {target_duration_seconds}\n"
        f"ASSET_UNDERSTANDINGS: {json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        plan = NarrativePlan.model_validate(provider.generate_structured_json(
            system_prompt=AUTO_NARRATIVE_SCRIPT_SYSTEM, user_prompt=user_prompt, response_schema=narrative_response_schema(),
        ))
    except (TypeError, ValueError) as exc:
        raise AutoNarrativeError("Narrative provider returned an invalid script plan") from exc
    allowed = {item.asset_id for item in understandings}
    if any(beat.asset_id not in allowed for beat in plan.beats):
        raise AutoNarrativeError("Narrative script referenced an asset outside the selected set")
    return plan


def select_source_window(asset: "MediaAsset", preferred_start: float, preferred_end: float, output_duration: float) -> tuple[float, float]:
    duration = float(asset.duration_seconds or 0)
    if duration < output_duration:
        raise AutoNarrativeError(f"{asset.filename} is too short for its generated narration segment")
    preferred_center = (preferred_start + preferred_end) / 2
    start = min(max(0.0, preferred_center - output_duration / 2), duration - output_duration)
    return round(start, 3), round(start + output_duration, 3)
