"""Calibratable thin-lens/PSF reference math for manual-lens preview and offline rendering."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class LensPhysicsError(ValueError):
    pass


@dataclass(frozen=True)
class LensPhysicsProfile:
    focal_length_mm: float = 50.0
    f_number: float = 1.4
    sensor_width_mm: float = 36.0
    cauchy_a: float = 1.5046
    cauchy_b_um2: float = .00420
    cauchy_c_um4: float = .000012
    reference_wavelength_nm: float = 546.1
    red_wavelength_nm: float = 650.0
    blue_wavelength_nm: float = 450.0
    longitudinal_ca_strength: float = 1.0
    field_mtf_falloff: float = .35
    spherical_aberration_waves: float = .22
    psf_radius_px: int = 7

    def validate(self) -> None:
        if self.focal_length_mm <= 0 or self.f_number <= 0 or self.sensor_width_mm <= 0:
            raise LensPhysicsError("Focal length, f-number, and sensor width must be positive")
        if self.cauchy_a <= 1 or self.cauchy_b_um2 < 0 or self.cauchy_c_um4 < 0:
            raise LensPhysicsError("Cauchy refractive-index coefficients are invalid")
        if min(self.reference_wavelength_nm, self.red_wavelength_nm, self.blue_wavelength_nm) <= 0:
            raise LensPhysicsError("Wavelengths must be positive")
        if not 1 <= self.psf_radius_px <= 15 or not 0 <= self.field_mtf_falloff <= 1 or self.spherical_aberration_waves < 0:
            raise LensPhysicsError("PSF radius or aberration values are out of range")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def refractive_index_cauchy(wavelength_nm: float, profile: LensPhysicsProfile) -> float:
    """n(λ)=A+B/λ²+C/λ⁴, with λ represented in micrometres."""
    profile.validate(); wavelength_um = wavelength_nm / 1000.0
    return profile.cauchy_a + profile.cauchy_b_um2 / wavelength_um**2 + profile.cauchy_c_um4 / wavelength_um**4


def longitudinal_focus_shift_mm(wavelength_nm: float, profile: LensPhysicsProfile) -> float:
    """Thin-lens chromatic focal shift from wavelength-dependent optical power.

    f_λ≈f_ref (n_ref−1)/(n_λ−1), therefore Δf_λ=f_λ−f_ref.
    """
    reference_n = refractive_index_cauchy(profile.reference_wavelength_nm, profile)
    wavelength_n = refractive_index_cauchy(wavelength_nm, profile)
    focal_at_wavelength = profile.focal_length_mm * (reference_n - 1.0) / max(wavelength_n - 1.0, 1e-7)
    return (focal_at_wavelength - profile.focal_length_mm) * profile.longitudinal_ca_strength


def chromatic_scale_offsets(profile: LensPhysicsProfile) -> dict[str, float]:
    """Radial image-scale offsets used by shaders; values multiply field position in pixels."""
    return {
        "red": longitudinal_focus_shift_mm(profile.red_wavelength_nm, profile) / profile.focal_length_mm,
        "green": 0.0,
        "blue": longitudinal_focus_shift_mm(profile.blue_wavelength_nm, profile) / profile.focal_length_mm,
    }


def psf_kernel(profile: LensPhysicsProfile, *, frame_width_px: int) -> Any:
    """Diffraction Airy PSF times a spherical-aberration envelope, normalised for convolution.

    MTF(f)=|F{PSF(x)}| is exposed through ``mtf_curve`` for chart/profile calibration.
    """
    try:
        import numpy as np
        from scipy.special import j1
    except ImportError as exc:
        raise LensPhysicsError("NumPy and SciPy are required for physical PSF/MTF calculation") from exc
    profile.validate()
    pixel_pitch_um = profile.sensor_width_mm * 1000.0 / max(frame_width_px, 1)
    airy_radius_px = 1.22 * (profile.reference_wavelength_nm / 1000.0) * profile.f_number / max(pixel_pitch_um, 1e-6)
    radius = profile.psf_radius_px; yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1].astype(np.float32)
    radial_px = np.sqrt(xx**2 + yy**2); argument = np.pi * radial_px / max(airy_radius_px, 1e-5)
    airy = np.ones_like(argument); mask = argument > 1e-5; airy[mask] = (2 * j1(argument[mask]) / argument[mask]) ** 2
    aberration_sigma = max(.05, profile.spherical_aberration_waves * (.35 + .65 * profile.field_mtf_falloff) * 2.0)
    aberration = np.exp(-(radial_px**2) / (2 * aberration_sigma**2))
    kernel = airy * aberration; kernel /= max(float(kernel.sum()), 1e-8)
    return kernel.astype(np.float32)


def mtf_curve(kernel: Any, *, samples: int = 64) -> list[dict[str, float]]:
    """Radially averaged normalised FFT magnitude: MTF(f)=|F{PSF}|."""
    import numpy as np

    padded = np.zeros((max(128, kernel.shape[0] * 8), max(128, kernel.shape[1] * 8)), dtype=np.float32)
    h, w = kernel.shape; y, x = (padded.shape[0] - h) // 2, (padded.shape[1] - w) // 2; padded[y:y + h, x:x + w] = kernel
    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(np.fft.ifftshift(padded)))); spectrum /= max(float(spectrum.max()), 1e-8)
    yy, xx = np.mgrid[0:padded.shape[0], 0:padded.shape[1]]; radius = np.sqrt((xx - padded.shape[1] / 2) ** 2 + (yy - padded.shape[0] / 2) ** 2)
    maximum = min(padded.shape) / 2; output: list[dict[str, float]] = []
    for index in range(samples):
        low, high = index / samples * maximum, (index + 1) / samples * maximum; selected = spectrum[(radius >= low) & (radius < high)]
        output.append({"normalised_frequency": round(index / samples, 5), "mtf": round(float(selected.mean()) if selected.size else 0.0, 6)})
    return output


def apply_loca_linear(rgb: Any, profile: LensPhysicsProfile) -> Any:
    """CPU reference for radial R/B image-plane offsets around the optical axis."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise LensPhysicsError("OpenCV and NumPy are required for LoCA rendering") from exc
    height, width = rgb.shape[:2]; yy, xx = np.mgrid[0:height, 0:width].astype(np.float32); cx, cy = (width - 1) / 2, (height - 1) / 2
    offsets = chromatic_scale_offsets(profile); output = rgb.copy()
    for channel, scale in ((0, offsets["red"]), (2, offsets["blue"])):
        map_x, map_y = cx + (xx - cx) * (1 + scale), cy + (yy - cy) * (1 + scale)
        output[..., channel] = cv2.remap(rgb[..., channel], map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
    return output
