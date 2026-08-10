"""Face-aware reaction/gaming layout analysis for a 9:16 stacked output."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class VerticalDualLayoutError(RuntimeError):
    pass


def _deps() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VerticalDualLayoutError("OpenCV and NumPy are required for dual-screen analysis") from exc
    return cv2, np


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


@dataclass(frozen=True)
class CropRegion:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class VerticalDualLayoutPlan:
    source_width: int
    source_height: int
    face_crop: CropRegion
    gameplay_crop: CropRegion
    top_ratio: float
    face_detected: bool
    confidence: float

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _crop_around(width: int, height: int, *, aspect: float, center_x: float, center_y: float) -> CropRegion:
    if width / height >= aspect:
        crop_height, crop_width = height, _even(height * aspect)
    else:
        crop_width, crop_height = width, _even(width / aspect)
    crop_width, crop_height = min(width, crop_width), min(height, crop_height)
    x = max(0, min(width - crop_width, int(round(center_x - crop_width / 2))))
    y = max(0, min(height - crop_height, int(round(center_y - crop_height / 2))))
    return CropRegion(x - x % 2, y - y % 2, _even(crop_width), _even(crop_height))


def analyze_vertical_dual_layout(video_path: str | Path, *, top_ratio: float = .43, max_samples: int = 48) -> VerticalDualLayoutPlan:
    """Find a stable face region and a motion-weighted gameplay focus from a 16:9 source."""
    if not .30 <= top_ratio <= .60 or not 8 <= max_samples <= 180:
        raise ValueError("top_ratio or max_samples is out of range")
    cv2, np = _deps(); capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise VerticalDualLayoutError(f"Unable to open video: {video_path}")
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if width < 2 or height < 2:
        capture.release(); raise VerticalDualLayoutError("Video has invalid dimensions")
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    stride = max(1, frame_count // max_samples) if frame_count else 6
    faces: list[tuple[float, float, float, float]] = []; motion_centers: list[tuple[float, float]] = []; previous_gray = None; index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok: break
            if index % stride:
                index += 1; continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            detected = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(32, 32)) if not detector.empty() else []
            face = max(detected, key=lambda item: int(item[2]) * int(item[3])) if len(detected) else None
            if face is not None:
                x, y, face_width, face_height = (float(value) for value in face); faces.append((x, y, face_width, face_height))
            if previous_gray is not None:
                diff = cv2.absdiff(gray, previous_gray); _, mask = cv2.threshold(diff, 22, 255, cv2.THRESH_BINARY)
                if face is not None:
                    x, y, face_width, face_height = (int(value) for value in face)
                    pad = int(max(face_width, face_height) * .8)
                    mask[max(0, y - pad):min(height, y + face_height + pad), max(0, x - pad):min(width, x + face_width + pad)] = 0
                moments = cv2.moments(mask)
                if moments["m00"] > 2500:
                    motion_centers.append((moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]))
            previous_gray = gray; index += 1
    finally:
        capture.release()
    if faces:
        median_face = np.median(np.asarray(faces), axis=0); face_center_x = float(median_face[0] + median_face[2] / 2); face_center_y = float(median_face[1] + median_face[3] / 2)
        face_detected, confidence = True, min(.95, .45 + len(faces) / max(1, index / stride) * .5)
    else:
        # Safe fallback: a reaction camera is commonly in an upper corner; keep a wide top crop.
        face_center_x, face_center_y, face_detected, confidence = width * .25, height * .30, False, .28
    if motion_centers:
        game_center_x, game_center_y = (float(value) for value in np.median(np.asarray(motion_centers), axis=0))
    else:
        game_center_x = width * (.68 if face_center_x < width / 2 else .32); game_center_y = height * .55
    # Each panel occupies the full vertical width, so its crop ratio is derived from its actual panel height.
    face_crop = _crop_around(width, height, aspect=9 / (16 * top_ratio), center_x=face_center_x, center_y=face_center_y)
    gameplay_crop = _crop_around(width, height, aspect=9 / (16 * (1 - top_ratio)), center_x=game_center_x, center_y=game_center_y)
    return VerticalDualLayoutPlan(width, height, face_crop, gameplay_crop, top_ratio, face_detected, round(confidence, 3))
