"""MediaPipe Pose repetition counting using deliberately conservative joint-angle state machines."""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


class FitnessRepError(RuntimeError):
    pass


Exercise = Literal["squat", "bench_press", "deadlift"]


@dataclass(frozen=True)
class RepEvent:
    rep: int
    source_time: float
    cycle_seconds: float
    angle_degrees: float
    fatigue: bool = False

    def to_json(self) -> dict[str, object]:
        return {key: round(value, 3) if isinstance(value, float) else value for key, value in asdict(self).items()}


def _dependencies() -> tuple[Any, Any, Any]:
    try:
        import cv2
        import mediapipe as mp
        import numpy as np
    except ImportError as exc:
        raise FitnessRepError("MediaPipe, OpenCV and NumPy are required for fitness rep analysis") from exc
    return cv2, mp, np


def _angle(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    left, right = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    denominator = math.hypot(*left) * math.hypot(*right)
    if denominator <= 1e-8: return 180.0
    cosine = max(-1.0, min(1.0, (left[0] * right[0] + left[1] * right[1]) / denominator))
    return math.degrees(math.acos(cosine))


def _visible(landmarks: list[Any], indexes: tuple[int, int, int]) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None:
    points = [landmarks[index] for index in indexes]
    if any(float(point.visibility) < .58 for point in points): return None
    return tuple((float(point.x), float(point.y)) for point in points)  # type: ignore[return-value]


def _exercise_angle(landmarks: list[Any], exercise: Exercise, pose_landmark: Any) -> float | None:
    if exercise == "bench_press":
        sides = ((pose_landmark.LEFT_SHOULDER, pose_landmark.LEFT_ELBOW, pose_landmark.LEFT_WRIST), (pose_landmark.RIGHT_SHOULDER, pose_landmark.RIGHT_ELBOW, pose_landmark.RIGHT_WRIST))
    elif exercise == "deadlift":
        sides = ((pose_landmark.LEFT_SHOULDER, pose_landmark.LEFT_HIP, pose_landmark.LEFT_KNEE), (pose_landmark.RIGHT_SHOULDER, pose_landmark.RIGHT_HIP, pose_landmark.RIGHT_KNEE))
    else:
        sides = ((pose_landmark.LEFT_HIP, pose_landmark.LEFT_KNEE, pose_landmark.LEFT_ANKLE), (pose_landmark.RIGHT_HIP, pose_landmark.RIGHT_KNEE, pose_landmark.RIGHT_ANKLE))
    values = [_angle(*points) for indexes in sides if (points := _visible(landmarks, tuple(int(item) for item in indexes))) is not None]
    return sum(values) / len(values) if values else None


def analyze_repetitions(video_path: str | Path, *, exercise: Exercise, sample_every_n_frames: int = 3, fatigue_ratio: float = 1.25) -> list[RepEvent]:
    if sample_every_n_frames < 1: raise ValueError("sample_every_n_frames must be positive")
    cv2, mp, _ = _dependencies(); capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened(): raise FitnessRepError("Unable to open fitness source video")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0; frame_index = 0; phase = "up"; bottom_time: float | None = None; previous_completion: float | None = None; events: list[RepEvent] = []
    # Hysteresis makes the counter robust to tiny landmark jitter at the top/bottom of a rep.
    down_threshold, up_threshold = (102.0, 158.0) if exercise in {"squat", "deadlift"} else (88.0, 152.0)
    pose = mp.solutions.pose.Pose(static_image_mode=False, model_complexity=1, smooth_landmarks=True, min_detection_confidence=.58, min_tracking_confidence=.58)
    try:
        while True:
            ok, frame = capture.read()
            if not ok: break
            if frame_index % sample_every_n_frames:
                frame_index += 1; continue
            timestamp = frame_index / fps; frame_index += 1
            result = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if not result.pose_landmarks: continue
            angle = _exercise_angle(result.pose_landmarks.landmark, exercise, mp.solutions.pose.PoseLandmark)
            if angle is None: continue
            if phase == "up" and angle <= down_threshold:
                phase = "down"; bottom_time = timestamp
            elif phase == "down" and angle >= up_threshold:
                # A cycle shorter than 0.45s is usually detection noise rather than a completed lift.
                cycle = timestamp - (previous_completion if previous_completion is not None else bottom_time or timestamp)
                if cycle >= .45:
                    events.append(RepEvent(rep=len(events) + 1, source_time=timestamp, cycle_seconds=cycle, angle_degrees=angle))
                    previous_completion = timestamp
                phase = "up"; bottom_time = None
    finally:
        pose.close(); capture.release()
    if len(events) >= 2:
        baseline = statistics.median([event.cycle_seconds for event in events[:-1]] or [events[-1].cycle_seconds])
        final = events[-1]
        if final.cycle_seconds >= baseline * fatigue_ratio:
            events[-1] = RepEvent(**{**asdict(final), "fatigue": True})
    return events


def map_reps_to_timeline(events: list[RepEvent], document: dict[str, Any], source_asset_id: str) -> list[dict[str, object]]:
    """Map source timestamps through selected main-track in/out points to final Timeline time."""
    source_windows: list[tuple[float, float, float]] = []; cursor = 0.0
    tracks = document.get("tracks")
    if not isinstance(tracks, list):
        tracks = [{"type": "main_video", "clips": list(document.get("segments", []))}]
    for track in tracks:
        if not isinstance(track, dict) or track.get("type") != "main_video": continue
        for clip in track.get("clips", []):
            if not isinstance(clip, dict) or clip.get("action", "keep") != "keep": continue
            start, end = float(clip.get("source_start", 0)), float(clip.get("source_end", 0)); output_start = float(clip.get("timeline_start", cursor))
            if str(clip.get("source_asset_id", source_asset_id)) == source_asset_id and end > start: source_windows.append((start, end, output_start))
            cursor = max(cursor, output_start) + max(0.0, end - start)
    mapped: list[dict[str, object]] = []
    for event in events:
        for start, end, output_start in source_windows:
            if start <= event.source_time <= end:
                mapped.append({**event.to_json(), "timeline_time": round(output_start + event.source_time - start, 3)}); break
    return mapped
