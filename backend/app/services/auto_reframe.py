"""Person-aware 9:16 auto-reframing with MediaPipe and Kalman-smoothed crop boxes.

The generated sendcmd file drives FFmpeg's named crop filter.  Commands use the
*source* timestamps, so it must be inserted before timeline trim/concat filters.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


class AutoReframeError(RuntimeError):
    pass


def _load_cv_dependencies() -> tuple[Any, Any]:
    """Defer optional CV imports so ordinary API workers can still import render code."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise AutoReframeError("OpenCV and NumPy are required; install backend requirements") from exc
    return cv2, np


@dataclass(frozen=True)
class CropBox:
    timestamp: float
    x: int
    y: int
    width: int
    height: int
    detected: bool


@dataclass(frozen=True)
class AutoReframePlan:
    source_width: int
    source_height: int
    crop_width: int
    crop_height: int
    boxes: list[CropBox]

    def to_json(self) -> dict:
        return {
            "source_width": self.source_width,
            "source_height": self.source_height,
            "crop_width": self.crop_width,
            "crop_height": self.crop_height,
            "boxes": [asdict(box) for box in self.boxes],
        }


class ScalarKalman:
    """Small constant-velocity Kalman filter for one crop coordinate."""

    def __init__(self, position: float, process_noise: float = 3.0, measurement_noise: float = 45.0) -> None:
        _, np = _load_cv_dependencies()
        self._np = np
        self.state = np.array([[position], [0.0]], dtype=np.float64)
        self.covariance = np.eye(2, dtype=np.float64) * 100.0
        self.process_noise = process_noise
        self.measurement_noise = measurement_noise

    def update(self, measurement: float | None, dt: float) -> float:
        np = self._np
        transition = np.array([[1.0, dt], [0.0, 1.0]], dtype=np.float64)
        q = self.process_noise * max(dt, 1 / 60)
        process_covariance = np.array([[q * dt * dt, 0.0], [0.0, q]], dtype=np.float64)
        self.state = transition @ self.state
        self.covariance = transition @ self.covariance @ transition.T + process_covariance
        if measurement is not None:
            observation = np.array([[1.0, 0.0]], dtype=np.float64)
            innovation = measurement - float(observation @ self.state)
            innovation_covariance = float(observation @ self.covariance @ observation.T) + self.measurement_noise
            gain = (self.covariance @ observation.T) / innovation_covariance
            self.state += gain * innovation
            self.covariance = (np.eye(2) - gain @ observation) @ self.covariance
        return float(self.state[0, 0])


class MediaPipeSpeakerDetector:
    """Fuse a face box with visible pose landmarks to preserve face and gestures."""

    def __init__(self, min_detection_confidence: float = 0.55) -> None:
        try:
            import mediapipe as mp
        except ImportError as exc:  # Keeps app imports functional in slim local environments.
            raise AutoReframeError("MediaPipe is required; install backend requirements") from exc
        self.face = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=min_detection_confidence
        )
        self.pose = mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            smooth_landmarks=True,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_detection_confidence,
        )

    def close(self) -> None:
        self.face.close()
        self.pose.close()

    def detect_center(self, bgr_frame: np.ndarray) -> tuple[float, float] | None:
        cv2, _ = _load_cv_dependencies()
        height, width = bgr_frame.shape[:2]
        rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        face_result = self.face.process(rgb_frame)
        pose_result = self.pose.process(rgb_frame)
        points: list[tuple[float, float, float]] = []

        if face_result.detections:
            # MediaPipe may return several people; the largest face is the most reliable
            # consumer-video proxy for the active vlogger / foreground subject.
            detection = max(
                face_result.detections,
                key=lambda item: item.location_data.relative_bounding_box.width * item.location_data.relative_bounding_box.height,
            )
            face_box = detection.location_data.relative_bounding_box
            points.append(((face_box.xmin + face_box.width / 2) * width, (face_box.ymin + face_box.height / 2) * height, 2.2))

        if pose_result.pose_landmarks:
            for landmark in pose_result.pose_landmarks.landmark:
                if landmark.visibility >= 0.55:
                    points.append((landmark.x * width, landmark.y * height, float(landmark.visibility)))

        if not points:
            return None
        # The face has a higher weight, while the pose prevents hand gestures being cropped out.
        total_weight = sum(weight for _, _, weight in points)
        return (
            sum(x * weight for x, _, weight in points) / total_weight,
            sum(y * weight for _, y, weight in points) / total_weight,
        )


def _even(value: int) -> int:
    return max(2, value - value % 2)


def crop_dimensions(source_width: int, source_height: int, aspect_ratio: tuple[int, int] = (9, 16)) -> tuple[int, int]:
    """Largest even crop contained by the source with the requested aspect ratio."""
    ratio = aspect_ratio[0] / aspect_ratio[1]
    if source_width / source_height >= ratio:
        return _even(int(source_height * ratio)), _even(source_height)
    return _even(source_width), _even(int(source_width / ratio))


def analyze_video_for_reframe(
    video_path: str | Path,
    *,
    aspect_ratio: tuple[int, int] = (9, 16),
    detector_stride: int = 1,
    smoothing: float = .75,
    max_pan_speed_px_per_second: float = 720.0,
) -> AutoReframePlan:
    """Decode frames, detect a speaker, and return a crop box for each decoded frame.

    ``detector_stride`` may be increased for long videos; intermediate frames retain
    Kalman prediction, preserving a frame-level crop timeline without expensive CV.
    """
    if detector_stride < 1 or not 0 <= smoothing <= 1 or max_pan_speed_px_per_second <= 0:
        raise ValueError("Invalid detector stride, smoothing, or pan speed")
    cv2, np = _load_cv_dependencies()
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise AutoReframeError(f"Unable to open video: {video_path}")
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    if source_width < 2 or source_height < 2:
        capture.release()
        raise AutoReframeError("Video has invalid dimensions")
    crop_width, crop_height = crop_dimensions(source_width, source_height, aspect_ratio)
    # Higher smoothing trusts motion prediction more than noisy detector measurements.
    process_noise = 1.0 + 10.0 * (1.0 - smoothing)
    measurement_noise = 12.0 + 160.0 * smoothing
    x_filter = ScalarKalman((source_width - crop_width) / 2, process_noise, measurement_noise)
    y_filter = ScalarKalman((source_height - crop_height) / 2, process_noise, measurement_noise)
    detector = MediaPipeSpeakerDetector()
    boxes: list[CropBox] = []
    frame_index = 0
    previous_x = previous_y = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            center = detector.detect_center(frame) if frame_index % detector_stride == 0 else None
            target_x = None if center is None else center[0] - crop_width / 2
            target_y = None if center is None else center[1] - crop_height * 0.42
            x = x_filter.update(target_x, 1 / fps)
            y = y_filter.update(target_y, 1 / fps)
            x = int(np.clip(round(x), 0, source_width - crop_width))
            y = int(np.clip(round(y), 0, source_height - crop_height))
            max_step = max_pan_speed_px_per_second / fps
            if previous_x is not None:
                x = int(np.clip(x, previous_x - max_step, previous_x + max_step))
            if previous_y is not None:
                y = int(np.clip(y, previous_y - max_step, previous_y + max_step))
            # Crop coordinates must be even for common H.264 4:2:0 encoders.
            x, y = x - x % 2, y - y % 2
            if previous_x is not None and abs(x - previous_x) < 2:
                x = previous_x
            if previous_y is not None and abs(y - previous_y) < 2:
                y = previous_y
            previous_x, previous_y = x, y
            boxes.append(CropBox(frame_index / fps, x, y, crop_width, crop_height, center is not None))
            frame_index += 1
    finally:
        capture.release()
        detector.close()
    if not boxes:
        raise AutoReframeError("Video contains no decodable frames")
    return AutoReframePlan(source_width, source_height, crop_width, crop_height, boxes)


def write_sendcmd_file(plan: AutoReframePlan, destination: str | Path, *, crop_filter_name: str = "auto_reframe") -> Path:
    """Write timestamped x/y commands, omitting unchanged positions to keep it compact."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous: tuple[int, int] | None = None
    lines: list[str] = []
    for box in plan.boxes:
        position = (box.x, box.y)
        if position == previous:
            continue
        lines.append(f"{box.timestamp:.6f} crop@{crop_filter_name} x {box.x}, crop@{crop_filter_name} y {box.y};")
        previous = position
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_reframe_plan_json(plan: AutoReframePlan, destination: str | Path) -> Path:
    path = Path(destination)
    path.write_text(json.dumps(plan.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate a MediaPipe-based 9:16 auto-reframe plan")
    parser.add_argument("input_video")
    parser.add_argument("--sendcmd", required=True, help="Output FFmpeg sendcmd file")
    parser.add_argument("--plan-json", required=True, help="Output crop plan JSON")
    parser.add_argument("--detector-stride", type=int, default=1)
    args = parser.parse_args()
    generated_plan = analyze_video_for_reframe(args.input_video, detector_stride=args.detector_stride)
    write_sendcmd_file(generated_plan, args.sendcmd)
    write_reframe_plan_json(generated_plan, args.plan_json)
    print(json.dumps(generated_plan.to_json(), ensure_ascii=False))
