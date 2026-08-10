"""Dense Farneback flow, flow-guided interpolation, and optional motion-blur compensation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class OpticalFlowError(RuntimeError):
    pass


@dataclass(frozen=True)
class FarnebackConfig:
    pyr_scale: float = 0.5
    levels: int = 4
    winsize: int = 21
    iterations: int = 5
    poly_n: int = 7
    poly_sigma: float = 1.5


def compute_dense_flow(previous_bgr: Any, next_bgr: Any, config: FarnebackConfig = FarnebackConfig()) -> Any:
    import cv2
    previous = cv2.cvtColor(previous_bgr, cv2.COLOR_BGR2GRAY)
    following = cv2.cvtColor(next_bgr, cv2.COLOR_BGR2GRAY)
    return cv2.calcOpticalFlowFarneback(
        previous, following, None, config.pyr_scale, config.levels, config.winsize,
        config.iterations, config.poly_n, config.poly_sigma, 0,
    )


def interpolate_frame(previous: Any, following: Any, forward_flow: Any, backward_flow: Any, t: float) -> Any:
    import cv2
    import numpy as np

    if not 0 < t < 1:
        return previous if t <= 0 else following
    height, width = previous.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    from_previous = cv2.remap(previous, grid_x - forward_flow[..., 0] * t, grid_y - forward_flow[..., 1] * t, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    from_following = cv2.remap(following, grid_x - backward_flow[..., 0] * (1 - t), grid_y - backward_flow[..., 1] * (1 - t), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    return cv2.addWeighted(from_previous, 1 - t, from_following, t, 0)


def motion_blur_from_flow(frame: Any, forward_flow: Any, shutter_fraction: float = 0.5, samples: int = 8) -> Any:
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    accumulated = np.zeros_like(frame, dtype=np.float32)
    for offset in np.linspace(-0.5, 0.5, max(2, samples)) * shutter_fraction:
        accumulated += cv2.remap(frame, grid_x - forward_flow[..., 0] * offset, grid_y - forward_flow[..., 1] * offset, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101).astype(np.float32)
    return np.clip(accumulated / max(2, samples), 0, 255).astype(frame.dtype)


def retime_video_with_flow(
    input_path: str | Path, output_path: str | Path, *, slow_motion_factor: float,
    apply_motion_blur: bool = False, config: FarnebackConfig = FarnebackConfig(),
) -> dict[str, float]:
    """Write a silent flow-interpolated video; caller can time-stretch/mux the original audio."""
    import cv2

    if not 1.0 < slow_motion_factor <= 8.0:
        raise OpticalFlowError("slow_motion_factor must be between 1 and 8")
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise OpticalFlowError("Unable to open video for optical-flow retiming")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        capture.release()
        raise OpticalFlowError("Unable to create optical-flow output video")
    fractional_accumulator = 0.0
    ok, previous = capture.read()
    frame_count = 0
    try:
        while ok:
            next_ok, following = capture.read()
            if not next_ok:
                writer.write(previous)
                frame_count += 1
                break
            forward = compute_dense_flow(previous, following, config)
            backward = compute_dense_flow(following, previous, config)
            fractional_accumulator += slow_motion_factor
            output_count = max(1, int(fractional_accumulator))
            fractional_accumulator -= output_count
            writer.write(previous)
            frame_count += 1
            for index in range(1, output_count):
                generated = interpolate_frame(previous, following, forward, backward, index / output_count)
                writer.write(motion_blur_from_flow(generated, forward) if apply_motion_blur else generated)
                frame_count += 1
            previous, ok = following, True
    finally:
        capture.release()
        writer.release()
    return {"fps": fps, "frames": float(frame_count), "slow_motion_factor": slow_motion_factor}
