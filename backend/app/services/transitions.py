"""Shared transition presets plus depth/flow transition asset generation."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Literal

from app.schemas.transitions import TransitionSpec
from app.services.virtual_relighting import RelativeDepthEstimator, _dependencies


class TransitionError(RuntimeError):
    pass


PRESET_EXPORTS: dict[str, dict[str, str]] = {
    "crossfade": {"xfade": "fade", "shader": "crossfade"},
    "glitch": {"xfade": "dissolve", "shader": "glitch"},
    "rgb_split": {"xfade": "fade", "shader": "rgb_split"},
    "zoom_blur": {"xfade": "zoomin", "shader": "zoom_blur"},
    "depth_person_through": {"xfade": "smoothleft", "shader": "depth_person_through"},
    "depth_background_peel": {"xfade": "dissolve", "shader": "depth_background_peel"},
    "morph_cut": {"xfade": "fade", "shader": "morph_cut"},
}


def ffmpeg_transition_filter(spec: TransitionSpec, *, offset_seconds: float, gltransition_available: bool = False) -> str:
    """Compile the same shader id used by WebGL into gltransition, or a deterministic xfade fallback."""
    preset = PRESET_EXPORTS[spec.kind]
    if gltransition_available and spec.kind in {"glitch", "rgb_split", "zoom_blur"}:
        source = spec.shader_id or f"{preset['shader']}.glsl"
        return f"gltransition=duration={spec.duration_seconds:.6f}:offset={offset_seconds:.6f}:source='{source}'"
    transition = spec.fallback_xfade or preset["xfade"]
    return f"xfade=transition={transition}:duration={spec.duration_seconds:.6f}:offset={offset_seconds:.6f}"


def _frame_at(video_path: str | Path, time_seconds: float) -> Any:
    cv2, _ = _dependencies()
    capture = cv2.VideoCapture(str(video_path)); capture.set(cv2.CAP_PROP_POS_MSEC, time_seconds * 1000)
    ok, frame = capture.read(); capture.release()
    if not ok:
        raise TransitionError("Could not decode transition boundary frame")
    return frame


def _smoothstep(edge0: float, edge1: float, value: Any) -> Any:
    _, np = _dependencies(); value = np.clip((value - edge0) / max(edge1 - edge0, 1e-6), 0, 1)
    return value * value * (3 - 2 * value)


def _face_mask(frame: Any) -> Any:
    cv2, np = _dependencies(); height, width = frame.shape[:2]
    mask = np.zeros((height, width), dtype=np.float32)
    try:
        import mediapipe as mp
        with mp.solutions.face_detection.FaceDetection(model_selection=0, min_detection_confidence=.5) as detector:
            result = detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        if result.detections:
            box = result.detections[0].location_data.relative_bounding_box
            x, y, w, h = int(box.xmin * width), int(box.ymin * height), int(box.width * width), int(box.height * height)
            cv2.ellipse(mask, (max(0, x + w // 2), max(0, y + h // 2)), (max(12, w // 2), max(12, h // 2)), 0, 0, 360, 1, -1)
    except ImportError:
        pass
    if not mask.any():
        cv2.ellipse(mask, (width // 2, height // 3), (width // 4, height // 3), 0, 0, 360, 1, -1)
    return cv2.GaussianBlur(mask, (0, 0), max(8, min(width, height) / 35))


def render_transition_asset(
    kind: Literal["depth_person_through", "depth_background_peel", "morph_cut"],
    source_path: str | Path, target_path: str | Path, *, from_time: float, to_time: float,
    duration_seconds: float, output_path: str | Path, fps: float = 30.0, depth_model: str = "auto",
) -> dict[str, Any]:
    """Render a silent transition plate from boundary frames; final timeline retains the original audio crossfade."""
    cv2, np = _dependencies()
    outgoing, incoming = _frame_at(source_path, from_time), _frame_at(target_path, to_time)
    if outgoing.shape != incoming.shape:
        incoming = cv2.resize(incoming, (outgoing.shape[1], outgoing.shape[0]), interpolation=cv2.INTER_CUBIC)
    height, width = outgoing.shape[:2]; count = max(2, round(duration_seconds * fps))
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    metadata: dict[str, Any] = {"kind": kind, "fps": fps, "frame_count": count}
    try:
        if kind.startswith("depth_"):
            estimator = RelativeDepthEstimator(depth_model)
            depth = estimator.estimate(outgoing)
            # Near pixels transition at a different point in time, giving the wipe a physical depth ordering.
            ordering = depth if kind == "depth_person_through" else 1 - depth
            for index in range(count):
                progress = index / (count - 1)
                reveal = _smoothstep(progress - .16, progress + .16, ordering)[..., None]
                frame = outgoing.astype(np.float32) * (1 - reveal) + incoming.astype(np.float32) * reveal
                writer.write(np.clip(frame, 0, 255).astype(np.uint8))
            metadata["depth_model"] = estimator.name
        else:
            gray_a, gray_b = cv2.cvtColor(outgoing, cv2.COLOR_BGR2GRAY), cv2.cvtColor(incoming, cv2.COLOR_BGR2GRAY)
            flow_a = cv2.calcOpticalFlowFarneback(gray_a, gray_b, None, .5, 3, 21, 3, 5, 1.2, 0)
            flow_b = cv2.calcOpticalFlowFarneback(gray_b, gray_a, None, .5, 3, 21, 3, 5, 1.2, 0)
            xx, yy = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32)); mask = _face_mask(outgoing)[..., None]
            for index in range(count):
                progress = index / (count - 1)
                warped_a = cv2.remap(outgoing, xx - flow_a[..., 0] * progress, yy - flow_a[..., 1] * progress, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
                warped_b = cv2.remap(incoming, xx - flow_b[..., 0] * (1 - progress), yy - flow_b[..., 1] * (1 - progress), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
                morph = warped_a.astype(np.float32) * (1 - progress) + warped_b.astype(np.float32) * progress
                normal = outgoing.astype(np.float32) * (1 - progress) + incoming.astype(np.float32) * progress
                writer.write(np.clip(normal * (1 - mask) + morph * mask, 0, 255).astype(np.uint8))
            metadata["flow"] = "farneback_dense_face_weighted"
    finally:
        writer.release()
    return metadata
