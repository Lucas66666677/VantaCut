from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Timeline, User
from app.schemas.film_optics import FilmOpticsMasterRequest, FilmOpticsMasterResponse
from app.services.film_optics import FilmOpticsSettings
from app.services.lens_physics import (
    LensPhysicsProfile,
    chromatic_scale_offsets,
    longitudinal_focus_shift_mm,
    mtf_curve,
    psf_kernel,
)


router = APIRouter(prefix="/timelines", tags=["film-optics"])


@router.put("/{timeline_id}/film-optics-master", response_model=FilmOpticsMasterResponse)
def update_film_optics_master(
    timeline_id: UUID, payload: FilmOpticsMasterRequest, db: Session = Depends(get_db)
) -> FilmOpticsMasterResponse:
    timeline = db.get(Timeline, timeline_id)
    user = db.get(User, payload.user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this timeline")
    values = payload.model_dump(mode="json", exclude={"user_id"})
    settings = FilmOpticsSettings(**values)
    settings.validate()
    timeline.settings_json = {**dict(timeline.settings_json or {}), "film_optics_master": settings.to_dict()}
    db.commit()
    return FilmOpticsMasterResponse(timeline_id=timeline.id, status="configured", settings=settings.to_dict())


@router.get("/{timeline_id}/film-optics-mtf")
def get_film_optics_mtf(
    timeline_id: UUID,
    user_id: UUID,
    frame_width_px: int = 1920,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Return the calibrated PSF-derived MTF and LoCA values for preview controls.

    The response is intentionally a compact curve rather than a full kernel; the
    browser receives the kernel through the normal profile/render payload.
    """
    if not 320 <= frame_width_px <= 8192:
        raise HTTPException(status_code=422, detail="frame_width_px must be between 320 and 8192")
    timeline = db.get(Timeline, timeline_id)
    user = db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None or timeline.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="User cannot read this timeline")

    configured = dict((timeline.settings_json or {}).get("film_optics_master") or {})
    settings = FilmOpticsSettings(**configured)
    profile = LensPhysicsProfile(
        focal_length_mm=settings.focal_length_mm,
        f_number=settings.aperture_f_number,
        sensor_width_mm=settings.sensor_width_mm,
        cauchy_a=settings.cauchy_a,
        cauchy_b_um2=settings.cauchy_b_um2,
        cauchy_c_um4=settings.cauchy_c_um4,
        longitudinal_ca_strength=settings.longitudinal_ca_strength,
        field_mtf_falloff=settings.edge_mtf_falloff,
        spherical_aberration_waves=settings.spherical_aberration,
        psf_radius_px=settings.psf_kernel_radius_px,
    )
    kernel = psf_kernel(profile, frame_width_px=frame_width_px)
    return {
        "physical_psf_enabled": settings.physical_psf_enabled,
        "profile": profile.to_dict(),
        "kernel_size": int(kernel.shape[0]),
        "mtf": mtf_curve(kernel),
        "chromatic_scale_offsets": chromatic_scale_offsets(profile),
        "longitudinal_focus_shift_mm": {
            "red": longitudinal_focus_shift_mm(profile.red_wavelength_nm, profile),
            "blue": longitudinal_focus_shift_mm(profile.blue_wavelength_nm, profile),
        },
    }
