"""Depth-aware optical look simulation for preview/render workers (OpenCV/NumPy)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpticalEffectSettings:
    chromatic_aberration_px: float = 1.2
    vignette_strength: float = 0.35
    vignette_power: float = 2.2
    bokeh_radius_px: float = 12.0
    focus_depth: float = 0.5
    aperture_f_number: float = 2.0
    focal_length_mm: float | None = None
    sensor_width_mm: float = 36.0
    focus_distance_m: float | None = None
    horizontal_fov_degrees: float | None = None


def apply_chromatic_aberration(frame: Any, pixels: float) -> Any:
    import cv2
    import numpy as np

    if pixels <= 0:
        return frame
    height, width = frame.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    center_x, center_y = (width - 1) / 2, (height - 1) / 2
    dx, dy = grid_x - center_x, grid_y - center_y
    radius = np.sqrt(dx * dx + dy * dy) / max(np.sqrt(center_x * center_x + center_y * center_y), 1)
    shift_x, shift_y = pixels * radius * dx / max(center_x, 1), pixels * radius * dy / max(center_y, 1)
    blue, green, red = cv2.split(frame)
    red = cv2.remap(red, grid_x - shift_x, grid_y - shift_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    blue = cv2.remap(blue, grid_x + shift_x, grid_y + shift_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    return cv2.merge((blue, green, red))


def apply_vignetting(frame: Any, strength: float, power: float, horizontal_fov_degrees: float | None = None) -> Any:
    import numpy as np

    if strength <= 0:
        return frame
    height, width = frame.shape[:2]
    yy, xx = np.ogrid[:height, :width]
    radius = np.sqrt(((xx - width / 2) / (width / 2)) ** 2 + ((yy - height / 2) / (height / 2)) ** 2)
    if horizontal_fov_degrees:
        # cos^4 falloff is the first-order physical illumination model for an ideal lens.
        edge_angle = np.deg2rad(horizontal_fov_degrees / 2)
        physical_falloff = np.cos(np.arctan(np.clip(radius, 0, 1) * np.tan(edge_angle))) ** 4
        mask = (1 - strength) + strength * physical_falloff
    else:
        mask = 1.0 - min(max(strength, 0.0), 1.0) * np.clip(radius, 0, 1) ** max(power, 0.1)
    return np.clip(frame.astype(np.float32) * mask[..., None], 0, 255).astype(frame.dtype)


def apply_depth_aware_bokeh(frame: Any, relative_depth: Any, settings: OpticalEffectSettings) -> Any:
    import cv2
    import numpy as np

    if settings.bokeh_radius_px <= 0:
        return frame
    depth = np.clip(relative_depth.astype(np.float32), 1e-3, None)
    if settings.focus_distance_m and settings.focal_length_mm:
        # Thin-lens circle of confusion in mm, then projected into output pixels.
        focus_mm = settings.focus_distance_m * 1000.0
        focal_mm = settings.focal_length_mm
        depth_mm = depth * 1000.0
        coc_mm = abs((focal_mm * focal_mm * (depth_mm - focus_mm)) / (
            max(settings.aperture_f_number, 0.7) * depth_mm * max(focus_mm - focal_mm, 1e-3)
        ))
        coc = np.clip(coc_mm / max(settings.sensor_width_mm, 1e-3) * frame.shape[1] / max(settings.bokeh_radius_px, 1), 0, 1)
    else:
        # MiDaS output is relative depth; this branch is a perceptual approximation, not metrical optics.
        normalised = (depth - depth.min()) / max(float(depth.max() - depth.min()), 1e-6)
        focus = max(settings.focus_depth, 1e-3)
        coc = np.clip(abs(1.0 / np.clip(normalised, 1e-3, 1.0) - 1.0 / focus) * (2.0 / max(settings.aperture_f_number, 0.7)), 0, 1)
    radius = int(max(1, min(61, round(settings.bokeh_radius_px))))
    radius += 1 - radius % 2
    blurred = cv2.GaussianBlur(frame, (radius, radius), 0)
    return np.clip(frame.astype(np.float32) * (1 - coc[..., None]) + blurred.astype(np.float32) * coc[..., None], 0, 255).astype(frame.dtype)


def apply_optical_effects(frame: Any, settings: OpticalEffectSettings, *, relative_depth: Any | None = None) -> Any:
    result = apply_chromatic_aberration(frame, settings.chromatic_aberration_px)
    result = apply_vignetting(result, settings.vignette_strength, settings.vignette_power, settings.horizontal_fov_degrees)
    return apply_depth_aware_bokeh(result, relative_depth, settings) if relative_depth is not None else result
