import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.ai.bgm_prompts import BGM_SYSTEM_PROMPT, BGM_USER_PROMPT, bgm_response_schema
from app.ai.providers.factory import get_vision_provider
from app.schemas.bgm import BGMRecommendation
from app.schemas.subtitle import ConfirmedTimelineSegment


SAMPLE_COUNT = 8
FRAME_TIMEOUT_SECONDS = 120


class BGMRecommendationError(RuntimeError):
    pass


def kept_segments_from_timeline(raw_segments: list[dict[str, Any]]) -> list[ConfirmedTimelineSegment]:
    segments = [ConfirmedTimelineSegment.model_validate(segment) for segment in raw_segments]
    kept = sorted((segment for segment in segments if segment.action == "keep"), key=lambda segment: segment.source_start)
    if not kept:
        raise BGMRecommendationError("Confirmed timeline has no keep segments")
    return kept


def _pace_for_duration(duration: float) -> str:
    if duration < 3:
        return "fast"
    if duration <= 7:
        return "medium"
    return "slow"


def uniformly_sample_kept_timeline(
    segments: list[ConfirmedTimelineSegment], count: int = SAMPLE_COUNT
) -> list[dict[str, Any]]:
    """Sample output-time positions uniformly, then map each to its original source time."""
    total_duration = sum(segment.source_end - segment.source_start for segment in segments)
    if total_duration <= 0:
        raise BGMRecommendationError("Keep segments have no duration")
    samples: list[dict[str, Any]] = []
    for index in range(count):
        output_time = total_duration * (index + 0.5) / count
        cursor = 0.0
        for segment in segments:
            duration = segment.source_end - segment.source_start
            if output_time <= cursor + duration or segment is segments[-1]:
                source_time = segment.source_start + min(duration, output_time - cursor)
                samples.append({
                    "sample_index": index,
                    "output_time": round(output_time, 3),
                    "source_time": round(source_time, 3),
                    "pace": _pace_for_duration(duration),
                    "segment_duration": round(duration, 3),
                })
                break
            cursor += duration
    return samples


def extract_bgm_frames(video_path: Path, samples: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    for sample in samples:
        frame_path = output_dir / f"bgm-{sample['sample_index']:02d}.jpg"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", str(sample["source_time"]), "-i", str(video_path),
                    "-frames:v", "1", "-q:v", "3", str(frame_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=FRAME_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise BGMRecommendationError("Frame extraction timed out") from exc
        except subprocess.CalledProcessError as exc:
            raise BGMRecommendationError(f"Frame extraction failed: {(exc.stderr or '')[-500:]}") from exc
        if frame_path.exists():
            frames.append({**sample, "path": str(frame_path)})
    if len(frames) < 5:
        raise BGMRecommendationError("Could not extract the minimum five frames for BGM analysis")
    return frames


def recommend_bgm(
    video_uri: str,
    kept_segments: list[ConfirmedTimelineSegment],
    video_path: Path,
    workdir: Path,
) -> BGMRecommendation:
    samples = uniformly_sample_kept_timeline(kept_segments)
    frames = extract_bgm_frames(video_path, samples, workdir / "bgm-frames")
    provider = get_vision_provider()
    raw_result = provider.analyze_video(
        video_uri,
        f"{BGM_SYSTEM_PROMPT}\n\n{BGM_USER_PROMPT}",
        response_schema=bgm_response_schema(),
        context={
            "task": "bgm_recommendation",
            "sampled_frames": frames,
            "scene_pacing": [{
                "output_time": sample["output_time"],
                "pace": sample["pace"],
                "segment_duration": sample["segment_duration"],
            } for sample in samples],
        },
    )
    try:
        return BGMRecommendation.model_validate(raw_result)
    except Exception as exc:
        raise BGMRecommendationError("Multimodal provider returned invalid BGM recommendation JSON") from exc
