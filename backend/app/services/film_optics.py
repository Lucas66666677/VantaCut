"""Linear-light reference implementation for an analogue film / manual-lens master layer."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.services.lens_physics import LensPhysicsProfile, apply_loca_linear, psf_kernel


class FilmOpticsError(ValueError):
    pass


@dataclass(frozen=True)
class FilmOpticsSettings:
    enabled: bool = True
    grain_amount: float = .18
    grain_size_px: float = 1.2
    halation_strength: float = .22
    halation_threshold: float = .76
    halation_radius_px: float = 12.0
    aperture_f_number: float = 1.4
    maximum_aperture_f_number: float = 1.4
    spherical_aberration: float = .22
    edge_mtf_falloff: float = .30
    focus_distance_m: float = 1.5
    reference_focus_distance_m: float = 2.0
    focus_breathing: float = .015
    temporal_seed: int = 17
    physical_psf_enabled: bool = False
    focal_length_mm: float = 50.0
    sensor_width_mm: float = 36.0
    cauchy_a: float = 1.5046
    cauchy_b_um2: float = .00420
    cauchy_c_um4: float = .000012
    longitudinal_ca_strength: float = 1.0
    psf_kernel_radius_px: int = 7

    def validate(self) -> None:
        bounded = {
            "grain_amount": self.grain_amount, "halation_strength": self.halation_strength,
            "spherical_aberration": self.spherical_aberration, "edge_mtf_falloff": self.edge_mtf_falloff,
            "focus_breathing": self.focus_breathing,
        }
        if any(value < 0 or value > 1 for value in bounded.values()):
            raise FilmOpticsError("Film optics strengths must be in [0, 1]")
        if not 0 < self.halation_threshold < 1 or self.halation_radius_px <= 0 or self.grain_size_px <= 0:
            raise FilmOpticsError("Film optics thresholds and radii must be positive")
        if min(self.aperture_f_number, self.maximum_aperture_f_number, self.focus_distance_m, self.reference_focus_distance_m) <= 0:
            raise FilmOpticsError("Aperture and focus distances must be positive")
        if self.physical_psf_enabled:
            LensPhysicsProfile(focal_length_mm=self.focal_length_mm, f_number=self.aperture_f_number, sensor_width_mm=self.sensor_width_mm, cauchy_a=self.cauchy_a, cauchy_b_um2=self.cauchy_b_um2, cauchy_c_um4=self.cauchy_c_um4, longitudinal_ca_strength=self.longitudinal_ca_strength, field_mtf_falloff=self.edge_mtf_falloff, spherical_aberration_waves=self.spherical_aberration, psf_radius_px=self.psf_kernel_radius_px).validate()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _srgb_to_linear(rgb: Any) -> Any:
    import numpy as np
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def _linear_to_srgb(rgb: Any) -> Any:
    import numpy as np
    return np.where(rgb <= .0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0) ** (1 / 2.4) - .055)


def _focus_breathing(frame: Any, settings: FilmOpticsSettings) -> Any:
    import cv2

    scale = 1 + settings.focus_breathing * (settings.reference_focus_distance_m / settings.focus_distance_m - 1)
    scale = max(.94, min(1.06, scale))
    if abs(scale - 1) < 1e-4:
        return frame
    height, width = frame.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), 0, scale)
    return cv2.warpAffine(frame, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT101)


def apply_film_optics_frame(bgr_frame: Any, settings: FilmOpticsSettings, *, frame_index: int) -> Any:
    """Apply halation, exposure-dependent silver-grain proxy, and manual-lens defects in linear light."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise FilmOpticsError("OpenCV and NumPy are required for film optics rendering") from exc
    settings.validate()
    if not settings.enabled:
        return bgr_frame
    bgr = _focus_breathing(bgr_frame, settings)
    rgb = bgr[..., ::-1].astype(np.float32) / 255
    linear = _srgb_to_linear(rgb)
    height, width = linear.shape[:2]
    if settings.physical_psf_enabled:
        profile = LensPhysicsProfile(focal_length_mm=settings.focal_length_mm, f_number=settings.aperture_f_number, sensor_width_mm=settings.sensor_width_mm, cauchy_a=settings.cauchy_a, cauchy_b_um2=settings.cauchy_b_um2, cauchy_c_um4=settings.cauchy_c_um4, longitudinal_ca_strength=settings.longitudinal_ca_strength, field_mtf_falloff=settings.edge_mtf_falloff, spherical_aberration_waves=settings.spherical_aberration, psf_radius_px=settings.psf_kernel_radius_px)
        # Reference path: a measured/calculated PSF convolution plus wavelength-specific image-plane offsets.
        kernel = psf_kernel(profile, frame_width_px=width)
        linear = cv2.filter2D(linear, -1, kernel, borderType=cv2.BORDER_REFLECT101)
        linear = apply_loca_linear(linear, profile)
    luminance = linear[..., 0] * .2126 + linear[..., 1] * .7152 + linear[..., 2] * .0722
    threshold = settings.halation_threshold
    highlight = np.clip((luminance - threshold) / max(1e-5, 1 - threshold), 0, 1)
    # The red anti-halation layer is modelled as a soft highlight spill outside the source highlight.
    blurred_highlight = cv2.GaussianBlur(highlight, (0, 0), settings.halation_radius_px)
    halo = np.maximum(blurred_highlight - highlight * .35, 0)[..., None] * settings.halation_strength
    linear += halo * np.asarray([1.0, .20, .07], dtype=np.float32)

    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    radial = np.clip(np.sqrt(((xx - width / 2) / (width / 2)) ** 2 + ((yy - height / 2) / (height / 2)) ** 2), 0, 1)
    aperture_factor = (settings.maximum_aperture_f_number / settings.aperture_f_number) ** 2
    # Third-order transverse ray error is approximated by blur increasing with r^2 and aperture^2.
    softness = settings.spherical_aberration * aperture_factor * (.25 + .75 * radial**2)
    blurred = cv2.GaussianBlur(linear, (0, 0), max(.15, settings.halation_radius_px * .18))
    edge_softness = settings.edge_mtf_falloff * radial**2
    blend = np.clip(softness + edge_softness, 0, .92)[..., None]
    linear = linear * (1 - blend) + blurred * blend

    # Silver-halide grain is correlated, exposure-dependent, and seeded per frame so it evolves in motion.
    rng = np.random.default_rng(settings.temporal_seed + frame_index * 1_000_003)
    coarse_width, coarse_height = max(1, round(width / settings.grain_size_px)), max(1, round(height / settings.grain_size_px))
    coarse = rng.normal(0, 1, (coarse_height, coarse_width)).astype(np.float32)
    common = cv2.resize(coarse, (width, height), interpolation=cv2.INTER_CUBIC)[..., None]
    chroma = rng.normal(0, .22, linear.shape).astype(np.float32)
    exposure_weight = .25 + .75 * (1 - np.clip(luminance, 0, 1)) ** .65
    linear += (common + chroma) * (settings.grain_amount * .035 * exposure_weight[..., None])
    rgb = np.clip(_linear_to_srgb(np.clip(linear, 0, 1)), 0, 1)
    return (rgb[..., ::-1] * 255 + .5).astype(np.uint8)


def render_film_optics_video(input_path: str | Path, output_path: str | Path, settings: FilmOpticsSettings) -> None:
    """CPU reference renderer. Production GPU workers should use the matching GLSL master shader."""
    import cv2

    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise FilmOpticsError("Unable to open rendered video for film optics")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    try:
        frame_index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            writer.write(apply_film_optics_frame(frame, settings, frame_index=frame_index))
            frame_index += 1
    finally:
        capture.release()
        writer.release()
