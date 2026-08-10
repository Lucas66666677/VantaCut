"""Reference-image colour analysis and portable 3D .cube LUT generation.

This is an explainable colour-match baseline, not a GAN style-transfer model:
palette, white balance, contrast and saturation from the reference become a
matrix plus tone curve sampled into a standard LUT.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import subprocess
from pathlib import Path
from typing import Any


class LUTGenerationError(RuntimeError):
    pass


def _load_image_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
    except ImportError as exc:
        raise LUTGenerationError("OpenCV is required for reference-image analysis; install backend requirements") from exc
    return cv2, _load_numpy()


def _load_numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise LUTGenerationError("NumPy is required for LUT generation; install backend requirements") from exc
    return np


@dataclass(frozen=True)
class PaletteColor:
    rgb: tuple[int, int, int]
    weight: float


@dataclass(frozen=True)
class ColorStyleProfile:
    palette: list[PaletteColor]
    contrast: float
    gamma: float
    saturation: float
    channel_gains: tuple[float, float, float]
    lab_mean: tuple[float, float, float]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ColorMatchProfile:
    """Source-to-reference transform persisted with a generated LUT for auditability."""
    reference_style: ColorStyleProfile
    source_rgb_mean: tuple[float, float, float]
    reference_rgb_mean: tuple[float, float, float]
    color_matrix: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
    color_offset: tuple[float, float, float]
    shadow_bias: tuple[float, float, float]
    highlight_bias: tuple[float, float, float]
    channel_maps: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _sample_pixels(image, max_pixels: int = 100_000):
    np = _load_numpy()
    pixels = image.reshape(-1, 3)
    if len(pixels) <= max_pixels:
        return pixels
    indices = np.linspace(0, len(pixels) - 1, max_pixels, dtype=np.int32)
    return pixels[indices]


def extract_color_style(reference_image_path: str | Path, *, palette_size: int = 6) -> ColorStyleProfile:
    """Extract weighted RGB palette and robust contrast statistics from a screenshot."""
    if not 2 <= palette_size <= 16:
        raise ValueError("palette_size must be between 2 and 16")
    cv2, np = _load_image_dependencies()
    image = cv2.imread(str(reference_image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise LUTGenerationError(f"Unable to read reference image: {reference_image_path}")
    lab_image = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    hsv_image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2HSV)

    lab_pixels = _sample_pixels(lab_image).astype(np.float32)
    # kmeans in Lab matches perceptual colour distance much better than raw RGB.
    _, labels, centers = cv2.kmeans(
        lab_pixels,
        palette_size,
        None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.25),
        3,
        cv2.KMEANS_PP_CENTERS,
    )
    counts = np.bincount(labels.flatten(), minlength=palette_size)
    palette: list[PaletteColor] = []
    for center, count in sorted(zip(centers, counts, strict=True), key=lambda item: int(item[1]), reverse=True):
        lab_pixel = np.uint8([[np.clip(center, 0, 255)]])
        rgb = cv2.cvtColor(lab_pixel, cv2.COLOR_LAB2RGB)[0, 0]
        palette.append(PaletteColor(tuple(int(channel) for channel in rgb), float(count / len(labels))))

    lightness = lab_image[:, :, 0].astype(np.float32) / 255.0
    low, middle, high = np.percentile(lightness, [5, 50, 95])
    measured_contrast = float(high - low)
    # 0.60 is a neutral everyday-video contrast reference; clamp prevents crushed blacks.
    contrast = float(np.clip(measured_contrast / 0.60, 0.70, 1.45))
    gamma = float(np.clip(np.log(0.5) / np.log(max(middle, 0.05)), 0.75, 1.35))
    mean_saturation = float(np.mean(hsv_image[:, :, 1]) / 255.0)
    saturation = float(np.clip(0.55 + mean_saturation, 0.65, 1.40))

    mean_rgb = np.mean(rgb_image.reshape(-1, 3).astype(np.float32) / 255.0, axis=0)
    neutral = max(float(np.mean(mean_rgb)), 0.05)
    gains = tuple(float(value) for value in np.clip(mean_rgb / neutral, 0.72, 1.30))
    mean_lab = np.mean(lab_image.reshape(-1, 3).astype(np.float32), axis=0)
    return ColorStyleProfile(
        palette=palette,
        contrast=contrast,
        gamma=gamma,
        saturation=saturation,
        channel_gains=gains,
        lab_mean=tuple(float(value) for value in mean_lab),
    )


def _saturation_matrix(saturation: float, np):
    # Rec.709 luma weights. Matrix makes colour intensity controllable in linear RGB.
    luma = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    return (1.0 - saturation) * np.tile(luma, (3, 1)) + saturation * np.eye(3, dtype=np.float32)


def build_style_matrix(profile: ColorStyleProfile):
    """Return a 3x3 RGB matrix combining reference white balance and saturation."""
    np = _load_numpy()
    gains = np.diag(np.array(profile.channel_gains, dtype=np.float32))
    return _saturation_matrix(profile.saturation, np) @ gains


def _apply_style(rgb, profile: ColorStyleProfile, matrix, np):
    # Approximate sRGB EOTF; apply colour matrix in linear light, then a tone curve.
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    transformed = linear @ matrix.T
    transformed = np.clip((transformed - 0.5) * profile.contrast + 0.5, 0.0, 1.0)
    transformed = np.clip(transformed, 0.0, 1.0) ** (1.0 / profile.gamma)
    return np.where(transformed <= 0.0031308, transformed * 12.92, 1.055 * transformed ** (1 / 2.4) - 0.055)


def generate_cube_lut(
    profile: ColorStyleProfile,
    output_path: str | Path,
    *,
    size: int = 33,
    title: str = "AI Reference Colour Match",
) -> Path:
    """Sample the style transform into an industry-standard IRIDAS .cube LUT."""
    if size not in {17, 33, 65}:
        raise ValueError("LUT size must be 17, 33, or 65")
    np = _load_numpy()
    matrix = build_style_matrix(profile)
    lines = [f'TITLE "{title.replace(chr(34), "")}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0", ""]
    values = np.linspace(0.0, 1.0, size, dtype=np.float32)
    # .cube convention: R changes fastest, then G, then B.
    for blue in values:
        for green in values:
            rgb = np.column_stack((values, np.full(size, green), np.full(size, blue)))
            converted = _apply_style(rgb, profile, matrix, np)
            lines.extend(f"{red:.6f} {green_value:.6f} {blue_value:.6f}" for red, green_value, blue_value in converted)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def _image_statistics(image_path: str | Path) -> dict[str, Any]:
    cv2, np = _load_image_dependencies()
    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise LUTGenerationError(f"Unable to read image: {image_path}")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    pixels = _sample_pixels(rgb, max_pixels=160_000).astype(np.float32)
    luma = pixels @ np.array([.2126, .7152, .0722], dtype=np.float32)
    shadow_cut, highlight_cut = np.quantile(luma, [.18, .82])
    shadows = pixels[luma <= shadow_cut] if np.any(luma <= shadow_cut) else pixels
    highlights = pixels[luma >= highlight_cut] if np.any(luma >= highlight_cut) else pixels
    cdfs: list[Any] = []
    for channel in range(3):
        histogram, _ = np.histogram(pixels[:, channel], bins=256, range=(0, 1))
        cdfs.append(np.cumsum(histogram, dtype=np.float64) / max(1, histogram.sum()))
    return {
        "mean": np.mean(pixels, axis=0), "std": np.maximum(np.std(pixels, axis=0), .035),
        "shadow_mean": np.mean(shadows, axis=0), "highlight_mean": np.mean(highlights, axis=0), "cdfs": cdfs,
    }


def _histogram_map(source_cdf, reference_cdf, np):
    """Map every 8-bit source code value to the reference CDF's inverse value."""
    source_values = np.linspace(0.0, 1.0, 256)
    reference_values = np.linspace(0.0, 1.0, 256)
    return np.interp(source_cdf, reference_cdf, reference_values, left=0.0, right=1.0).astype(np.float32)


def extract_color_match(source_image_path: str | Path, reference_image_path: str | Path) -> ColorMatchProfile:
    """Estimate an explainable histogram + matrix transform from a source frame to a reference screenshot."""
    np = _load_numpy()
    source, reference = _image_statistics(source_image_path), _image_statistics(reference_image_path)
    # A bounded diagonal affine matrix stabilises a CDF match when source footage has a narrow gamut.
    gains = np.clip(reference["std"] / source["std"], .65, 1.55)
    matrix = np.diag(gains).astype(np.float32)
    offset = np.clip(reference["mean"] - source["mean"] @ matrix.T, -.25, .25)
    maps = tuple(tuple(float(value) for value in _histogram_map(source["cdfs"][channel], reference["cdfs"][channel], np)) for channel in range(3))
    shadow_bias = np.clip(reference["shadow_mean"] - reference["mean"], -.18, .18)
    highlight_bias = np.clip(reference["highlight_mean"] - reference["mean"], -.18, .18)
    return ColorMatchProfile(
        reference_style=extract_color_style(reference_image_path),
        source_rgb_mean=tuple(float(value) for value in source["mean"]), reference_rgb_mean=tuple(float(value) for value in reference["mean"]),
        color_matrix=tuple(tuple(float(value) for value in row) for row in matrix), color_offset=tuple(float(value) for value in offset),
        shadow_bias=tuple(float(value) for value in shadow_bias), highlight_bias=tuple(float(value) for value in highlight_bias), channel_maps=maps,  # type: ignore[arg-type]
    )


def _smoothstep(edge0, edge1, values, np):
    t = np.clip((values - edge0) / max(1e-6, edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def generate_color_match_cube(profile: ColorMatchProfile, output_path: str | Path, *, size: int = 33) -> Path:
    """Generate a 3D LUT from channel CDF matching, affine colour mapping, and tonal colour casts."""
    if size not in {17, 33, 65}:
        raise ValueError("LUT size must be 17, 33, or 65")
    np = _load_numpy()
    channel_maps = np.asarray(profile.channel_maps, dtype=np.float32)
    matrix = np.asarray(profile.color_matrix, dtype=np.float32); offset = np.asarray(profile.color_offset, dtype=np.float32)
    shadow_bias = np.asarray(profile.shadow_bias, dtype=np.float32); highlight_bias = np.asarray(profile.highlight_bias, dtype=np.float32)
    lines = ['TITLE "AI Reference Color Match"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0", ""]
    values = np.linspace(0.0, 1.0, size, dtype=np.float32)
    for blue in values:
        for green in values:
            rgb = np.column_stack((values, np.full(size, green), np.full(size, blue)))
            mapped = np.column_stack([np.interp(rgb[:, channel], np.linspace(0.0, 1.0, 256), channel_maps[channel]) for channel in range(3)])
            affine = rgb @ matrix.T + offset
            converted = .72 * mapped + .28 * affine
            luma = converted @ np.array([.2126, .7152, .0722], dtype=np.float32)
            converted += (1.0 - _smoothstep(.0, .34, luma, np))[:, None] * shadow_bias * .52
            converted += _smoothstep(.67, 1.0, luma, np)[:, None] * highlight_bias * .52
            converted = np.clip(converted, 0.0, 1.0)
            lines.extend(f"{red:.6f} {green_value:.6f} {blue_value:.6f}" for red, green_value, blue_value in converted)
    destination = Path(output_path); destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def extract_video_reference_frame(video_path: str | Path, output_path: str | Path, *, seek_seconds: float = .5) -> Path:
    """Decode one source frame for LUT calibration without loading an entire video into memory."""
    destination = Path(output_path); destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{max(0.0, seek_seconds):.3f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(destination)],
            check=True, capture_output=True, text=True, timeout=45,
        )
    except subprocess.TimeoutExpired as exc:
        raise LUTGenerationError("Source-frame extraction timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise LUTGenerationError((exc.stderr or "Unable to decode a source frame")[-1200:]) from exc
    if not destination.is_file():
        raise LUTGenerationError("FFmpeg did not output a source frame")
    return destination


def write_style_profile(profile: ColorStyleProfile, output_path: str | Path) -> Path:
    destination = Path(output_path)
    destination.write_text(json.dumps(profile.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    return destination
