"""Relative-depth virtual relighting reference pipeline.

This is screen-space relighting, not a replacement for multi-view geometry: monocular depth
has unknown scale and cannot reveal surfaces hidden from the camera.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Any, Callable

from app.core.config import settings
from app.services.optical_metadata import MidasDepthEstimator


class VirtualRelightError(RuntimeError):
    pass


@dataclass(frozen=True)
class VirtualLight:
    enabled: bool = True
    screen_x: float = 0.5
    screen_y: float = 0.25
    depth: float = 0.20  # relative camera-space depth; not metres
    intensity: float = 1.0
    color_temperature_kelvin: int = 5600
    radius: float = 0.35
    volumetric_strength: float = 0.12
    shadow_strength: float = 0.35

    def validate(self) -> None:
        if not 0 <= self.screen_x <= 1 or not 0 <= self.screen_y <= 1 or not 0 <= self.depth <= 1:
            raise VirtualRelightError("Virtual light screen position and relative depth must be in [0, 1]")
        if not 0 <= self.intensity <= 8 or not .01 <= self.radius <= 2:
            raise VirtualRelightError("Virtual light intensity/radius is outside the safe range")
        if not 1000 <= self.color_temperature_kelvin <= 20000:
            raise VirtualRelightError("Color temperature must be between 1000K and 20000K")
        if not 0 <= self.volumetric_strength <= 2 or not 0 <= self.shadow_strength <= 1:
            raise VirtualRelightError("Volumetric and shadow strengths are outside the safe range")


@dataclass(frozen=True)
class VirtualRelightSettings:
    enabled: bool = True
    depth_model: str = "auto"  # auto | depth_anything | midas_small
    temporal_depth_smoothing: float = 0.72
    ambient_strength: float = 0.0
    lights: tuple[VirtualLight, ...] = (VirtualLight(),)

    def validate(self) -> None:
        if self.depth_model not in {"auto", "depth_anything", "midas_small"}:
            raise VirtualRelightError("depth_model must be auto, depth_anything, or midas_small")
        if not 0 <= self.temporal_depth_smoothing < 1 or not -1 <= self.ambient_strength <= 2:
            raise VirtualRelightError("Invalid temporal depth smoothing or ambient strength")
        if not self.lights or len(self.lights) > 4:
            raise VirtualRelightError("Configure between one and four virtual lights")
        for light in self.lights:
            light.validate()

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "lights": [asdict(light) for light in self.lights]}


def settings_from_dict(value: dict[str, Any]) -> VirtualRelightSettings:
    """Hydrate JSON settings without trusting nested dictionaries as dataclass instances."""
    return VirtualRelightSettings(
        enabled=bool(value.get("enabled", True)),
        depth_model=str(value.get("depth_model", "auto")),
        temporal_depth_smoothing=float(value.get("temporal_depth_smoothing", .72)),
        ambient_strength=float(value.get("ambient_strength", 0)),
        lights=tuple(VirtualLight(**dict(light)) for light in value.get("lights", [])),
    )


def _dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VirtualRelightError("OpenCV and NumPy are required for virtual relighting") from exc
    return cv2, np


class DepthAnythingOnnxEstimator:
    """Depth Anything V2 ONNX adapter; model file is operator supplied and remains provider-neutral."""

    def __init__(self, model_path: str) -> None:
        self.model_path = model_path
        self._session: Any | None = None

    def _load(self) -> None:
        if self._session is not None:
            return
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise VirtualRelightError("onnxruntime is required for Depth Anything ONNX inference") from exc
        if not Path(self.model_path).is_file():
            raise VirtualRelightError("DEPTH_ANYTHING_ONNX_PATH does not point to a readable ONNX model")
        self._session = ort.InferenceSession(self.model_path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])

    def estimate(self, bgr_frame: Any) -> Any:
        cv2, np = _dependencies()
        self._load()
        input_meta = self._session.get_inputs()[0]
        shape = input_meta.shape
        target_height = int(shape[2]) if isinstance(shape[2], int) else 518
        target_width = int(shape[3]) if isinstance(shape[3], int) else 518
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (target_width, target_height), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
        normalized = (resized - np.asarray([0.485, 0.456, 0.406], dtype=np.float32)) / np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
        output = self._session.run(None, {input_meta.name: normalized.transpose(2, 0, 1)[None]})[0]
        depth = np.squeeze(output).astype(np.float32)
        depth = cv2.resize(depth, (bgr_frame.shape[1], bgr_frame.shape[0]), interpolation=cv2.INTER_CUBIC)
        return _normalize_relative_depth(depth)


def _normalize_relative_depth(depth: Any) -> Any:
    _, np = _dependencies()
    low, high = np.percentile(depth, (1, 99))
    return np.clip((depth - low) / max(float(high - low), 1e-6), 0, 1).astype(np.float32)


class RelativeDepthEstimator:
    def __init__(self, model: str = "auto") -> None:
        self.model = model
        self._estimator: Any | None = None

    @property
    def name(self) -> str:
        if isinstance(self._estimator, DepthAnythingOnnxEstimator):
            return "depth_anything_v2_onnx"
        return "midas_small"

    def _resolve(self) -> Any:
        if self._estimator is not None:
            return self._estimator
        if self.model in {"auto", "depth_anything"} and settings.depth_anything_onnx_path:
            self._estimator = DepthAnythingOnnxEstimator(settings.depth_anything_onnx_path)
        elif self.model == "depth_anything":
            raise VirtualRelightError("depth_anything requested but DEPTH_ANYTHING_ONNX_PATH is not configured")
        else:
            self._estimator = MidasDepthEstimator("MiDaS_small")
        return self._estimator

    def estimate(self, bgr_frame: Any) -> Any:
        return _normalize_relative_depth(self._resolve().estimate(bgr_frame))


def depth_to_normals(relative_depth: Any, *, focal_length_px: float | None = None) -> Any:
    """Estimate camera-space normals from relative depth gradients under a pinhole-camera prior."""
    cv2, np = _dependencies()
    height, width = relative_depth.shape[:2]
    focal = focal_length_px or max(width, height)
    depth = cv2.GaussianBlur(relative_depth.astype(np.float32), (0, 0), 1.0)
    dzdx = cv2.Sobel(depth, cv2.CV_32F, 1, 0, ksize=3) * focal / max(width, 1)
    dzdy = cv2.Sobel(depth, cv2.CV_32F, 0, 1, ksize=3) * focal / max(height, 1)
    normals = np.dstack((-dzdx, -dzdy, np.ones_like(depth)))
    normals /= np.maximum(np.linalg.norm(normals, axis=2, keepdims=True), 1e-6)
    return normals.astype(np.float32)


def _srgb_to_linear(rgb: Any) -> Any:
    _, np = _dependencies()
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def _linear_to_srgb(rgb: Any) -> Any:
    _, np = _dependencies()
    return np.where(rgb <= .0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0) ** (1 / 2.4))


def _kelvin_to_rgb(kelvin: int) -> Any:
    _, np = _dependencies()
    temperature = max(1000, min(40000, kelvin)) / 100
    if temperature <= 66:
        red, green = 255, 99.4708025861 * math.log(temperature) - 161.1195681661
        blue = 0 if temperature <= 19 else 138.5177312231 * math.log(temperature - 10) - 305.0447927307
    else:
        red = 329.698727446 * ((temperature - 60) ** -0.1332047592)
        green = 288.1221695283 * ((temperature - 60) ** -0.0755148492)
        blue = 255
    return np.clip(np.asarray([red, green, blue], dtype=np.float32) / 255, 0, 1)


def _rgb_to_cct_kelvin(rgb: Any) -> int | None:
    _, np = _dependencies()
    r, g, b = np.clip(rgb, 0, 1)
    x = .4124 * r + .3576 * g + .1805 * b
    y = .2126 * r + .7152 * g + .0722 * b
    z = .0193 * r + .1192 * g + .9505 * b
    total = x + y + z
    if total <= 1e-6:
        return None
    chroma_x, chroma_y = x / total, y / total
    denominator = .1858 - chroma_y
    if abs(denominator) < 1e-6:
        return None
    n = (chroma_x - .3320) / denominator
    cct = 449 * n**3 + 3525 * n**2 + 6823.3 * n + 5520.33
    return int(np.clip(round(cct), 1000, 20000))


def estimate_key_light(bgr_frame: Any, normals: Any) -> dict[str, Any]:
    """Lambertian least-squares heuristic. It estimates direction, not a physically recoverable lamp position."""
    cv2, np = _dependencies()
    rgb = bgr_frame[..., ::-1].astype(np.float32) / 255
    linear = _srgb_to_linear(rgb)
    luminance = linear @ np.asarray([.2126, .7152, .0722], dtype=np.float32)
    saturation = rgb.max(axis=2) - rgb.min(axis=2)
    mask = (luminance > .08) & (luminance < .92) & (saturation < .32) & (normals[..., 2] > .1)
    if int(mask.sum()) < 200:
        return {"direction_camera": [0.0, -0.4, 0.9], "screen_space_position": [0.5, 0.38, 0.2], "color_temperature_kelvin": None, "color_rgb": [1.0, 1.0, 1.0], "confidence": 0.0, "limitations": "Not enough neutral diffuse pixels for a reliable key-light estimate."}
    sample_normals = normals[mask]
    design = np.column_stack((sample_normals, np.ones(len(sample_normals))))
    coefficients, *_ = np.linalg.lstsq(design, luminance[mask], rcond=None)
    direction = coefficients[:3]
    magnitude = float(np.linalg.norm(direction))
    if magnitude < 1e-6:
        direction = np.asarray([0, -.4, .9], dtype=np.float32)
        magnitude = 1
    direction /= magnitude
    residual = float(np.mean(np.abs(design @ coefficients - luminance[mask])))
    bright = linear[(luminance >= np.percentile(luminance, 93)) & (saturation < .45)]
    light_color = np.median(bright, axis=0) if len(bright) else np.ones(3, dtype=np.float32)
    light_color /= max(float(light_color.max()), 1e-6)
    return {"direction_camera": [round(float(item), 4) for item in direction], "screen_space_position": [round(float(np.clip(.5 + direction[0] * .32, 0, 1)), 4), round(float(np.clip(.5 - direction[1] * .32, 0, 1)), 4), round(float(np.clip(.5 - direction[2] * .25, 0, 1)), 4)], "relative_intensity": round(magnitude, 4), "ambient_estimate": round(float(coefficients[3]), 4), "color_rgb": [round(float(item), 4) for item in light_color], "color_temperature_kelvin": _rgb_to_cct_kelvin(light_color), "confidence": round(max(0.0, min(0.85, 1 - residual / max(float(luminance[mask].std()), .05))) * min(1, mask.sum() / 2000), 3), "limitations": "Direction/color are heuristic because albedo, exposure and monocular depth scale are unknown; screen_space_position is a visualisation, not metric lamp triangulation."}


def _screen_space_shadow(depth: Any, light: VirtualLight) -> Any:
    """Small screen-space ray march: nearby depth discontinuities attenuate the virtual light."""
    cv2, np = _dependencies()
    height, width = depth.shape
    xx, yy = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    delta_x, delta_y = (light.screen_x * width - xx), (light.screen_y * height - yy)
    distance = np.maximum(np.sqrt(delta_x * delta_x + delta_y * delta_y), 1.0)
    shadow = np.zeros_like(depth, dtype=np.float32)
    for step in range(1, 9):
        fraction = step / 9
        sample_x = np.clip((xx + delta_x * fraction).astype(np.int32), 0, width - 1)
        sample_y = np.clip((yy + delta_y * fraction).astype(np.int32), 0, height - 1)
        horizon = depth[sample_y, sample_x]
        # Relative near-depth occluders between a surface and projected lamp direction cast a soft screen-space shadow.
        shadow = np.maximum(shadow, np.clip((horizon - depth - .035) * 7, 0, 1) * (1 - fraction))
    return 1 - shadow * light.shadow_strength


def apply_virtual_relight_frame(bgr_frame: Any, relative_depth: Any, normals: Any, settings_value: VirtualRelightSettings) -> Any:
    cv2, np = _dependencies()
    settings_value.validate()
    if not settings_value.enabled:
        return bgr_frame
    height, width = relative_depth.shape
    rgb = bgr_frame[..., ::-1].astype(np.float32) / 255
    linear = _srgb_to_linear(rgb)
    xx, yy = np.meshgrid(np.linspace(-1, 1, width, dtype=np.float32), np.linspace(1, -1, height, dtype=np.float32))
    surface = np.dstack((xx, yy, 1 - relative_depth))
    additional = np.zeros_like(linear)
    depth_edges = cv2.GaussianBlur(np.abs(cv2.Laplacian(relative_depth, cv2.CV_32F)), (0, 0), 1.5)
    for light in settings_value.lights:
        if not light.enabled:
            continue
        lamp = np.asarray([light.screen_x * 2 - 1, 1 - light.screen_y * 2, 1 - light.depth], dtype=np.float32)
        vector = lamp - surface
        distance_squared = np.maximum((vector * vector).sum(axis=2), .015)
        direction = vector / np.sqrt(distance_squared)[..., None]
        diffuse = np.maximum((normals * direction).sum(axis=2), 0)
        attenuation = 1 / (1 + distance_squared / max(light.radius * light.radius, .01))
        shadow = _screen_space_shadow(relative_depth, light)
        color = _kelvin_to_rgb(light.color_temperature_kelvin)
        direct = diffuse * attenuation * shadow * light.intensity
        # A cheap single-scattering proxy: depth edges supply participating-medium density; the lamp-to-pixel ray is the march direction.
        beam_alignment = np.clip(1 - np.sqrt((xx - lamp[0]) ** 2 + (yy - lamp[1]) ** 2) / 1.8, 0, 1)
        volume = beam_alignment * (0.15 + np.clip(depth_edges * 5, 0, 1)) * light.volumetric_strength * attenuation
        additional += (direct + volume)[..., None] * color
    linear = np.clip(linear + additional + settings_value.ambient_strength, 0, 1)
    return (np.clip(_linear_to_srgb(linear), 0, 1)[..., ::-1] * 255 + .5).astype(np.uint8)


DepthFrameCallback = Callable[[float, Any, Any, dict[str, Any]], None]


def analyze_video_depth(video_path: str | Path, *, estimator: RelativeDepthEstimator, frame_stride: int = 1, smoothing: float = .72, on_frame: DepthFrameCallback | None = None) -> dict[str, Any]:
    cv2, np = _dependencies()
    if frame_stride < 1:
        raise ValueError("frame_stride must be >= 1")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise VirtualRelightError("Unable to decode video for depth analysis")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    previous_depth = None
    key_lights: list[dict[str, Any]] = []
    frame_index = processed = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % frame_stride:
                frame_index += 1
                continue
            depth = estimator.estimate(frame)
            if previous_depth is not None:
                depth = np.clip(smoothing * previous_depth + (1 - smoothing) * depth, 0, 1)
            previous_depth = depth
            normals = depth_to_normals(depth)
            key_light = estimate_key_light(frame, normals)
            timestamp = frame_index / fps
            if on_frame:
                on_frame(timestamp, depth, normals, key_light)
            key_lights.append(key_light)
            processed += 1
            frame_index += 1
    finally:
        capture.release()
    if not processed:
        raise VirtualRelightError("Video contained no decodable frames")
    confidence = sum(float(item["confidence"]) for item in key_lights) / len(key_lights)
    return {"depth_model": estimator.name, "depth_kind": "relative_monocular", "source_width": width, "source_height": height, "fps": fps, "frame_stride": frame_stride, "processed_frames": processed, "key_light_samples": key_lights, "key_light_confidence": round(confidence, 3), "limitations": "Relative monocular depth has no metric scale and cannot reconstruct hidden geometry. Light estimation is a Lambertian heuristic, not scene calibration."}


def render_virtual_relight_video(input_path: str | Path, output_path: str | Path, settings_value: VirtualRelightSettings) -> None:
    """CPU reference final renderer; GPU preview uses the matching WebGL shader."""
    cv2, _ = _dependencies()
    settings_value.validate()
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise VirtualRelightError("Unable to open video for virtual relighting")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    estimator = RelativeDepthEstimator(settings_value.depth_model)
    previous_depth = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            depth = estimator.estimate(frame)
            if previous_depth is not None:
                depth = settings_value.temporal_depth_smoothing * previous_depth + (1 - settings_value.temporal_depth_smoothing) * depth
            previous_depth = depth
            writer.write(apply_virtual_relight_frame(frame, depth, depth_to_normals(depth), settings_value))
    finally:
        capture.release()
        writer.release()
