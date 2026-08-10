"""Mask propagation and pluggable temporal video-inpainting runners.

The tracker is deliberately limited to a short editorial window.  It produces a mask for
each frame in that window; a GPU provider receives both neighbouring context and the mask
sequence so it can preserve temporal consistency instead of independently filling frames.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ProgressCallback = Callable[[float, str], None]


class VideoInpaintingError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalizedBox:
    x: float
    y: float
    width: float
    height: float

    def validate(self) -> None:
        if not (0 <= self.x < 1 and 0 <= self.y < 1 and 0 < self.width <= 1 and 0 < self.height <= 1):
            raise VideoInpaintingError("Mask coordinates must be normalized values in the range [0, 1]")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise VideoInpaintingError("Mask box extends beyond the frame")


@dataclass(frozen=True)
class TrackedMask:
    frame_index: int
    timestamp: float
    bbox: tuple[int, int, int, int]
    confidence: float
    mask_path: str


def _mask_from_box(height: int, width: int, box: NormalizedBox) -> Any:
    import cv2
    import numpy as np

    box.validate()
    x, y = round(box.x * width), round(box.y * height)
    right, bottom = round((box.x + box.width) * width), round((box.y + box.height) * height)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.rectangle(mask, (x, y), (max(x + 1, right), max(y + 1, bottom)), 255, thickness=-1)
    return mask


def _mask_from_strokes(height: int, width: int, strokes: list[dict[str, Any]]) -> Any:
    """Rasterise browser-normalised brush paths; dots and continuous strokes are both valid."""
    import cv2
    import numpy as np

    mask = np.zeros((height, width), dtype=np.uint8)
    short_edge = min(width, height)
    for stroke in strokes:
        points = list(stroke.get("points", []))
        if not points:
            continue
        radius = max(1, round(float(stroke.get("radius", .035)) * short_edge))
        path = [(round(float(point["x"]) * (width - 1)), round(float(point["y"]) * (height - 1))) for point in points]
        for point in path:
            cv2.circle(mask, point, radius, 255, thickness=-1, lineType=cv2.LINE_AA)
        for first, second in zip(path, path[1:]):
            cv2.line(mask, first, second, 255, thickness=radius * 2, lineType=cv2.LINE_AA)
    if not mask.any():
        raise VideoInpaintingError("Brush mask contains no drawable pixels")
    return mask


def _warp_mask_forward(mask: Any, flow: Any) -> Any:
    """Warp a binary mask with a dense Farneback flow field (previous -> current)."""
    import cv2
    import numpy as np

    height, width = mask.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    # cv.remap asks where each destination pixel came from; subtracting the forward field
    # gives a stable first-order inverse for the short frame-to-frame displacement here.
    warped = cv2.remap(mask, grid_x - flow[..., 0], grid_y - flow[..., 1], cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return cv2.morphologyEx(cv2.dilate(warped, kernel, iterations=1), cv2.MORPH_CLOSE, kernel)


def _bbox(mask: Any) -> tuple[int, int, int, int]:
    import cv2

    points = cv2.findNonZero(mask)
    if points is None:
        return (0, 0, 0, 0)
    x, y, width, height = cv2.boundingRect(points)
    return int(x), int(y), int(width), int(height)


def _flow_confidence(mask: Any, flow: Any) -> float:
    import cv2
    import numpy as np

    values = flow[mask > 0]
    if len(values) < 8:
        return 0.0
    median = np.median(values, axis=0)
    spread = float(np.median(np.linalg.norm(values - median, axis=1)))
    # A spatially coherent flow has small per-pixel deviation from its median displacement.
    return round(max(0.0, min(1.0, 1.0 - spread / 12.0)), 3)


def track_mask_window(
    video_path: str | Path,
    *,
    reference_time: float,
    initial_box: NormalizedBox | None,
    initial_strokes: list[dict[str, Any]] | None = None,
    output_dir: str | Path,
    progress: ProgressCallback | None = None,
) -> list[TrackedMask]:
    """Propagate a frame annotation forward and backward with dense optical flow."""
    import cv2

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise VideoInpaintingError("Unable to open inpainting context video")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count < 2:
        raise VideoInpaintingError("Inpainting context must contain at least two frames")
    frames: list[Any] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    finally:
        capture.release()
    if not frames:
        raise VideoInpaintingError("No decodable frames in inpainting context")

    reference_index = min(len(frames) - 1, max(0, round(reference_time * fps)))
    height, width = frames[0].shape[:2]
    masks: list[Any | None] = [None] * len(frames)
    if initial_strokes:
        masks[reference_index] = _mask_from_strokes(height, width, initial_strokes)
    elif initial_box is not None:
        masks[reference_index] = _mask_from_box(height, width, initial_box)
    else:
        raise VideoInpaintingError("An initial brush mask or box is required")
    flows: dict[int, Any] = {}

    # Forward propagation, then backward propagation with the corresponding directed flow.
    forward_steps = max(1, len(frames) - 1 - reference_index)
    for index in range(reference_index + 1, len(frames)):
        flow = cv2.calcOpticalFlowFarneback(frames[index - 1], frames[index], None, .5, 4, 21, 4, 7, 1.5, 0)
        flows[index] = flow
        masks[index] = _warp_mask_forward(masks[index - 1], flow)
        if progress:
            progress((index - reference_index) / forward_steps * .6, "tracking_forward")
    backward_steps = max(1, reference_index)
    for index in range(reference_index - 1, -1, -1):
        flow = cv2.calcOpticalFlowFarneback(frames[index + 1], frames[index], None, .5, 4, 21, 4, 7, 1.5, 0)
        flows[index] = flow
        masks[index] = _warp_mask_forward(masks[index + 1], flow)
        if progress:
            progress(.6 + (reference_index - index) / backward_steps * .4, "tracking_backward")

    tracked: list[TrackedMask] = []
    for index, mask in enumerate(masks):
        assert mask is not None
        mask_path = output / f"{index:06d}.png"
        cv2.imwrite(str(mask_path), mask)
        tracked.append(TrackedMask(
            frame_index=index,
            timestamp=round(index / fps, 4),
            bbox=_bbox(mask),
            confidence=1.0 if index == reference_index else _flow_confidence(mask, flows[index]),
            mask_path=str(mask_path),
        ))
    return tracked


def save_tracking_manifest(path: str | Path, tracked: list[TrackedMask], *, source_offset: float) -> None:
    frames = []
    for item in tracked:
        serialized = asdict(item)
        serialized["mask_name"] = Path(serialized.pop("mask_path")).name
        frames.append(serialized)
    Path(path).write_text(json.dumps({
        "version": 1,
        "source_offset": source_offset,
        "frames": frames,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


class VideoInpaintingProvider(ABC):
    name: str

    @abstractmethod
    def inpaint(self, *, video_path: Path, masks_dir: Path, output_path: Path, progress: ProgressCallback | None = None) -> None:
        """Produce a temporally-consistent repaired video; providers must not overwrite the source."""


class ProPainterCLIProvider(VideoInpaintingProvider):
    """Adapter around a locally provisioned ProPainter inference command.

    Configure PROPAINTER_COMMAND with `{input}`, `{masks}`, and `{output}` placeholders so this
    project does not silently depend on a fork-specific command-line interface.
    """

    name = "propainter"

    def __init__(self, command_template: str | None = None, timeout_seconds: int = 4 * 60 * 60) -> None:
        self.command_template = command_template or os.getenv("PROPAINTER_COMMAND", "")
        self.timeout_seconds = timeout_seconds

    def inpaint(self, *, video_path: Path, masks_dir: Path, output_path: Path, progress: ProgressCallback | None = None) -> None:
        required = {"{input}", "{masks}", "{output}"}
        if not self.command_template or not required.issubset(set(re.findall(r"\{[^}]+\}", self.command_template))):
            raise VideoInpaintingError("PROPAINTER_COMMAND must contain {input}, {masks}, and {output} placeholders")
        command = shlex.split(self.command_template.format(input=str(video_path), masks=str(masks_dir), output=str(output_path)))
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        output_lines: list[str] = []
        try:
            assert process.stdout is not None
            for line in process.stdout:
                output_lines.append(line)
                percent = re.search(r"(?:^|\s)(\d{1,3})%(?:\s|$)", line)
                if percent and progress:
                    progress(min(1.0, int(percent.group(1)) / 100), "propainter_inference")
            return_code = process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            raise VideoInpaintingError("ProPainter inference timed out") from exc
        if return_code != 0:
            raise VideoInpaintingError("ProPainter failed: " + "".join(output_lines)[-2000:])
        if not output_path.exists():
            raise VideoInpaintingError("ProPainter exited successfully but did not create the requested output")


def get_video_inpainting_provider() -> VideoInpaintingProvider:
    provider = os.getenv("VIDEO_INPAINTING_PROVIDER", "propainter").lower()
    if provider == "propainter":
        return ProPainterCLIProvider()
    raise VideoInpaintingError(f"Unsupported VIDEO_INPAINTING_PROVIDER: {provider}")
