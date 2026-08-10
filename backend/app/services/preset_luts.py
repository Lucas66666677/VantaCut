"""Consumer-friendly colour profiles sampled into portable IRIDAS .cube LUTs.

The profiles are intentionally small and deterministic: the API stores the
generated cube in object storage, so a render is independent of the browser
that selected the look.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PresetLUT:
    id: str
    name: str
    description: str
    accent: str
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    lift: float = 0.0
    red_gain: float = 1.0
    green_gain: float = 1.0
    blue_gain: float = 1.0


PRESET_LUTS: tuple[PresetLUT, ...] = (
    PresetLUT("vintage_film", "Vintage Film", "暖褐黑位與柔和底片感", "#b76d3b", 0.92, 0.78, 1.08, 0.035, 1.10, 1.00, 0.84),
    PresetLUT("nordic_cinematic", "Nordic Cinematic", "冷冽低飽和的北歐電影調", "#6f96a8", 1.10, 0.73, 1.03, 0.012, 0.86, 1.00, 1.13),
    PresetLUT("vibrant_vlog", "Vibrant Vlog", "明亮、飽和、適合日常 Vlog", "#ff7658", 1.06, 1.26, 1.04, 0.005, 1.06, 1.02, 0.98),
    PresetLUT("golden_hour", "Golden Hour", "金色夕陽與柔暖高光", "#eead4f", 0.98, 1.10, 1.10, 0.018, 1.16, 1.05, 0.82),
    PresetLUT("moody_teal", "Moody Teal", "青橙對比、濃郁戲劇感", "#237e88", 1.18, 0.92, 0.96, 0.0, 1.07, 1.01, 1.14),
    PresetLUT("soft_portrait", "Soft Portrait", "柔膚、低反差的人像質感", "#e8a9a7", 0.86, 0.88, 1.10, 0.040, 1.08, 1.01, 0.96),
    PresetLUT("noir_contrast", "Noir Contrast", "強烈黑白、都會敘事感", "#808080", 1.42, 0.03, 0.96, 0.0, 1.0, 1.0, 1.0),
    PresetLUT("pastel_dream", "Pastel Dream", "明亮奶油色與夢幻粉調", "#d891c5", 0.84, 0.74, 1.15, 0.052, 1.10, 1.02, 1.09),
    PresetLUT("urban_night", "Urban Night", "深藍夜景與霓虹氛圍", "#354a9c", 1.22, 1.13, 0.91, 0.0, 0.90, 0.96, 1.22),
    PresetLUT("clean_luxury", "Clean Luxury", "乾淨中性、精緻商業感", "#d5c6aa", 1.04, 0.88, 1.03, 0.008, 1.02, 1.01, 0.98),
)
_BY_ID = {item.id: item for item in PRESET_LUTS}


def get_preset_lut(preset_id: str) -> PresetLUT:
    try:
        return _BY_ID[preset_id]
    except KeyError as exc:
        raise ValueError(f"Unknown colour filter preset: {preset_id}") from exc


def preset_catalog() -> list[dict[str, str]]:
    return [
        {"id": item.id, "name": item.name, "description": item.description, "accent": item.accent}
        for item in PRESET_LUTS
    ]


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _grade(red: float, green: float, blue: float, profile: PresetLUT) -> tuple[float, float, float]:
    """A fast, safe display-space grade; it is sampled into a 3D lookup table."""
    red, green, blue = red * profile.red_gain, green * profile.green_gain, blue * profile.blue_gain
    red, green, blue = _clamp(red), _clamp(green), _clamp(blue)
    luma = red * 0.2126 + green * 0.7152 + blue * 0.0722
    red = luma + (red - luma) * profile.saturation
    green = luma + (green - luma) * profile.saturation
    blue = luma + (blue - luma) * profile.saturation
    red = (red - 0.5) * profile.contrast + 0.5 + profile.lift
    green = (green - 0.5) * profile.contrast + 0.5 + profile.lift
    blue = (blue - 0.5) * profile.contrast + 0.5 + profile.lift
    exponent = 1.0 / profile.gamma
    return tuple(_clamp(channel) ** exponent for channel in (red, green, blue))  # type: ignore[return-value]


def preset_lut_cube(preset_id: str, *, size: int = 17) -> str:
    """Build a small .cube file. 17³ keeps API/object-storage writes inexpensive."""
    if size not in {17, 33, 65}:
        raise ValueError("LUT size must be 17, 33, or 65")
    profile = get_preset_lut(preset_id)
    lines = [f'TITLE "{profile.name}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0", ""]
    values = [index / (size - 1) for index in range(size)]
    # IRIDAS .cube convention: red is the fastest-changing channel.
    for blue in values:
        for green in values:
            for red in values:
                converted = _grade(red, green, blue, profile)
                lines.append(f"{converted[0]:.6f} {converted[1]:.6f} {converted[2]:.6f}")
    return "\n".join(lines) + "\n"

