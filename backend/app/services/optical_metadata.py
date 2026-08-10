"""Lens metadata extraction and explicitly low-confidence monocular fallback estimates."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any


class OpticalMetadataError(RuntimeError):
    pass


def _as_float(value: Any) -> float | None:
    if value in (None, "", "-", "Unknown"):
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            return float(numerator) / float(denominator)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _exiftool_metadata(path: str | Path) -> dict[str, Any]:
    if shutil.which("exiftool") is None:
        return {}
    command = [
        "exiftool", "-j", "-n", "-FocalLength", "-FocalLengthIn35mmFormat", "-FNumber",
        "-ApertureValue", "-ExposureTime", "-ShutterSpeedValue", "-LensModel", "-LensID",
        "-Model", "-Make", "-ImageWidth", "-ImageHeight", str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=90)
        payload = json.loads(result.stdout)
        return payload[0] if payload else {}
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def _ffprobe_dimensions(path: str | Path) -> tuple[int | None, int | None]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        stream = (json.loads(result.stdout).get("streams") or [{}])[0]
        return int(stream.get("width") or 0) or None, int(stream.get("height") or 0) or None
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None, None


def _fov_degrees(focal_mm: float | None, focal_35mm: float | None) -> float | None:
    # 35 mm equivalent avoids assuming sensor size when a camera records this standard field.
    effective_focal = focal_35mm or focal_mm
    if effective_focal is None or effective_focal <= 0:
        return None
    return math.degrees(2 * math.atan(36.0 / (2 * effective_focal)))


def extract_optical_metadata(path: str | Path) -> dict[str, Any]:
    raw = _exiftool_metadata(path)
    width, height = _ffprobe_dimensions(path)
    focal_mm = _as_float(raw.get("FocalLength"))
    focal_35mm = _as_float(raw.get("FocalLengthIn35mmFormat"))
    aperture = _as_float(raw.get("FNumber")) or _as_float(raw.get("ApertureValue"))
    shutter_seconds = _as_float(raw.get("ExposureTime"))
    return {
        "source": "exiftool" if raw else "ffprobe_only",
        "confidence": "high" if focal_mm or focal_35mm else "none",
        "camera_make": raw.get("Make"), "camera_model": raw.get("Model"),
        "lens_model": raw.get("LensModel") or raw.get("LensID"),
        "focal_length_mm": focal_mm, "focal_length_35mm": focal_35mm,
        "aperture_f_number": aperture, "shutter_seconds": shutter_seconds,
        "shutter_speed_value": _as_float(raw.get("ShutterSpeedValue")),
        "horizontal_fov_degrees": _fov_degrees(focal_mm, focal_35mm),
        "width": width, "height": height,
        "raw": raw,
    }


class MidasDepthEstimator:
    """Lazy MiDaS depth inference. Values are relative depth, never metric distance."""

    def __init__(self, model_type: str = "MiDaS_small") -> None:
        self.model_type = model_type
        self._model: Any | None = None
        self._transform: Any | None = None
        self._torch: Any | None = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
        except ImportError as exc:
            raise OpticalMetadataError("PyTorch is required for MiDaS depth estimation") from exc
        self._torch = torch
        self._model = torch.hub.load("isl-org/MiDaS", self.model_type, trust_repo=True)
        transforms = torch.hub.load("isl-org/MiDaS", "transforms", trust_repo=True)
        self._transform = transforms.small_transform if "small" in self.model_type.lower() else transforms.dpt_transform
        self._model.eval()

    def estimate(self, bgr_frame: Any) -> Any:
        self._load()
        import cv2
        torch = self._torch
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        input_batch = self._transform(rgb)
        with torch.no_grad():
            prediction = self._model(input_batch)
            prediction = torch.nn.functional.interpolate(
                prediction.unsqueeze(1), size=bgr_frame.shape[:2], mode="bicubic", align_corners=False,
            ).squeeze()
        depth = prediction.cpu().numpy().astype("float32")
        return (depth - depth.min()) / max(float(depth.max() - depth.min()), 1e-6)


def estimate_optics_from_depth(bgr_frame: Any, relative_depth: Any) -> dict[str, Any]:
    """Heuristic FOV/distortion estimate; calibration requires known geometry and is not inferred here."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
    lines = cv2.HoughLinesP(cv2.Canny(gray, 60, 140), 1, np.pi / 180, threshold=60, minLineLength=50, maxLineGap=15)
    line_count = 0 if lines is None else len(lines)
    depth_spread = float(np.percentile(relative_depth, 90) - np.percentile(relative_depth, 10))
    # Wider fields tend to show stronger perspective/depth variation. This is deliberately conservative.
    fov = float(np.clip(55 + depth_spread * 22 + min(line_count, 80) * 0.08, 45, 100))
    # Straight-line curvature cannot be reliably solved from arbitrary footage; retain a small prior only.
    radial_k1 = float(np.clip((fov - 70) / 260, -0.12, 0.12))
    return {
        "source": "midas_relative_depth_plus_geometry_heuristic",
        "confidence": "low",
        "relative_depth_spread": depth_spread,
        "detected_line_count": line_count,
        "horizontal_fov_degrees": round(fov, 2),
        "radial_distortion_k1": round(radial_k1, 5),
        "limitations": "Monocular depth is relative. Exact focal length and distortion require lens calibration or known geometry.",
    }
