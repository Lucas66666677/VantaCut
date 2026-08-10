from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class FilmOpticsMasterRequest(BaseModel):
    user_id: UUID
    enabled: bool = True
    grain_amount: float = Field(default=.18, ge=0, le=1)
    grain_size_px: float = Field(default=1.2, gt=0, le=8)
    halation_strength: float = Field(default=.22, ge=0, le=1)
    halation_threshold: float = Field(default=.76, gt=0, lt=1)
    halation_radius_px: float = Field(default=12, gt=0, le=80)
    aperture_f_number: float = Field(default=1.4, gt=0, le=32)
    maximum_aperture_f_number: float = Field(default=1.4, gt=0, le=32)
    spherical_aberration: float = Field(default=.22, ge=0, le=1)
    edge_mtf_falloff: float = Field(default=.30, ge=0, le=1)
    focus_distance_m: float = Field(default=1.5, gt=0, le=10_000)
    reference_focus_distance_m: float = Field(default=2, gt=0, le=10_000)
    focus_breathing: float = Field(default=.015, ge=0, le=1)
    temporal_seed: int = Field(default=17, ge=0, le=2_147_483_647)
    physical_psf_enabled: bool = False
    focal_length_mm: float = Field(default=50, gt=1, le=2000)
    sensor_width_mm: float = Field(default=36, gt=1, le=100)
    cauchy_a: float = Field(default=1.5046, gt=1, le=3)
    cauchy_b_um2: float = Field(default=.00420, ge=0, le=1)
    cauchy_c_um4: float = Field(default=.000012, ge=0, le=1)
    longitudinal_ca_strength: float = Field(default=1, ge=0, le=4)
    psf_kernel_radius_px: int = Field(default=7, ge=1, le=15)

    @model_validator(mode="after")
    def aperture_is_physical(self) -> "FilmOpticsMasterRequest":
        if self.aperture_f_number < self.maximum_aperture_f_number:
            raise ValueError("aperture_f_number cannot be wider than maximum_aperture_f_number")
        return self


class FilmOpticsMasterResponse(BaseModel):
    timeline_id: UUID
    status: str
    settings: dict[str, object]
