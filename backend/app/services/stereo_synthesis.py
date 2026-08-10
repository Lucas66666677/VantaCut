"""Depth-guided 2D-to-stereo synthesis for a conservative virtual camera baseline.

Monocular depth has no absolute scale and cannot reveal true occluded background.
This module therefore treats IPD as a calibrated *virtual* baseline, caps disparity,
and records its assumptions instead of claiming a physically measured stereo rig.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from app.services.virtual_relighting import RelativeDepthEstimator


class StereoSynthesisError(RuntimeError):
    pass


@dataclass(frozen=True)
class StereoSynthesisSettings:
    ipd_mm: float = 63.5
    horizontal_fov_degrees: float = 80.0
    virtual_depth_range_m: float = 3.0
    max_disparity_px: float = 28.0
    temporal_depth_smoothing: float = .72
    depth_model: str = "auto"

    def validate(self) -> None:
        if not 50 <= self.ipd_mm <= 75 or not 35 < self.horizontal_fov_degrees < 130:
            raise StereoSynthesisError("IPD or horizontal FOV is outside the safe stereo range")
        if not .25 < self.virtual_depth_range_m <= 30 or not 2 <= self.max_disparity_px <= 96:
            raise StereoSynthesisError("Invalid virtual depth range or disparity cap")
        if not 0 <= self.temporal_depth_smoothing < 1:
            raise StereoSynthesisError("temporal_depth_smoothing must be in [0, 1)")


def _dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise StereoSynthesisError("OpenCV and NumPy are required for stereo synthesis") from exc
    return cv2, np


def _disparity_map(relative_depth: Any, width: int, settings: StereoSynthesisSettings) -> Any:
    """Approximate disparity from an IPD pinhole model with relative-depth scaling."""
    _, np = _dependencies()
    focal_px = width / (2 * math.tan(math.radians(settings.horizontal_fov_degrees) / 2))
    # Depth Anything/MiDaS expose relative depth. Higher normalised values are treated as nearer
    # after temporal normalisation; the cap protects viewers from vergence discomfort.
    virtual_depth_m = .45 + (1.0 - np.clip(relative_depth, 0, 1)) * settings.virtual_depth_range_m
    disparity = focal_px * (settings.ipd_mm / 1000.0) / np.maximum(virtual_depth_m, .1)
    return np.clip(disparity, 0, settings.max_disparity_px).astype(np.float32)


def synthesize_stereo_frame(frame: Any, relative_depth: Any, settings: StereoSynthesisSettings) -> tuple[Any, Any]:
    """Reproject source pixels into left/right virtual eyes using inverse depth-aware warps."""
    cv2, np = _dependencies()
    height, width = frame.shape[:2]
    disparity = _disparity_map(relative_depth, width, settings)
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    # Inverse mapping keeps every output pixel defined. Border replication avoids black slivers;
    # a true 3D capture or generative inpainting is required for physically correct disocclusion.
    left_map = grid_x + disparity / 2
    right_map = grid_x - disparity / 2
    left = cv2.remap(frame, left_map, grid_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    right = cv2.remap(frame, right_map, grid_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return left, right


def render_stereo_pair(
    input_path: str | Path,
    left_output_path: str | Path,
    right_output_path: str | Path,
    settings: StereoSynthesisSettings,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    """Render independently decodable left/right intermediates at source timing."""
    settings.validate()
    cv2, np = _dependencies()
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise StereoSynthesisError("Cannot open source render for stereo synthesis")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if width < 2 or height < 2:
        capture.release(); raise StereoSynthesisError("Source render has invalid dimensions")
    codec = cv2.VideoWriter_fourcc(*"mp4v")
    left_writer, right_writer = cv2.VideoWriter(str(left_output_path), codec, fps, (width, height)), cv2.VideoWriter(str(right_output_path), codec, fps, (width, height))
    if not left_writer.isOpened() or not right_writer.isOpened():
        capture.release(); left_writer.release(); right_writer.release()
        raise StereoSynthesisError("OpenCV cannot create stereo intermediates")
    estimator = RelativeDepthEstimator(settings.depth_model)
    previous_depth: Any | None = None
    processed = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            depth = estimator.estimate(frame)
            if previous_depth is not None:
                depth = previous_depth * settings.temporal_depth_smoothing + depth * (1 - settings.temporal_depth_smoothing)
            previous_depth = np.clip(depth, 0, 1).astype(np.float32)
            left, right = synthesize_stereo_frame(frame, previous_depth, settings)
            left_writer.write(left); right_writer.write(right)
            processed += 1
            if progress_callback and (processed % 15 == 0 or (frame_count and processed == frame_count)):
                progress_callback(int(processed / max(frame_count, processed) * 100))
    finally:
        capture.release(); left_writer.release(); right_writer.release()
    if processed == 0:
        raise StereoSynthesisError("No frames were decoded from source render")
    return {
        "width": width, "height": height, "fps": fps, "frames": processed,
        "settings": asdict(settings), "depth_model": estimator.name,
        "limitations": "Monocular depth is relative; disparity is a comfort-capped virtual IPD reprojection and cannot recover true disoccluded pixels.",
    }
