"""ACEScct colour-management primitives built on OpenColorIO v2."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings


class ColorManagementError(RuntimeError):
    pass


# Names must match the deployed ACES OCIO config; UI can expose these aliases.
CAMERA_LOG_COLORSPACES = {
    "slog3": "Sony S-Log3/S-Gamut3.Cine",
    "clog2": "Canon Log 2/Cinema Gamut",
    "logc3": "ARRI LogC3/EI800/AWG3",
    "vlog": "Panasonic V-Log/V-Gamut",
}


@dataclass(frozen=True)
class OCIOTransformSpec:
    input_color_space: str
    output_color_space: str = "ACEScct"
    cube_size: int = 65


def _ocio() -> Any:
    try:
        import PyOpenColorIO as ocio
    except ImportError as exc:
        raise ColorManagementError("OpenColorIO Python bindings are required") from exc
    return ocio


class ACESColorPipeline:
    """Transform pixels or bake portable LUTs from a studio-supplied ACES OCIO config."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = str(config_path or settings.ocio_config_path or "")
        if not self.config_path:
            raise ColorManagementError("OCIO_CONFIG_PATH must point to an ACES OCIO config.ocio")
        if not Path(self.config_path).is_file():
            raise ColorManagementError(f"OCIO config does not exist: {self.config_path}")
        ocio = _ocio()
        self.config = ocio.Config.CreateFromFile(self.config_path)

    def processor(self, input_color_space: str, output_color_space: str = "ACEScct") -> Any:
        try:
            return self.config.getProcessor(input_color_space, output_color_space).getDefaultCPUProcessor()
        except Exception as exc:
            raise ColorManagementError(
                f"OCIO config cannot transform {input_color_space!r} to {output_color_space!r}"
            ) from exc

    def transform_rgb_pixels(self, pixels: Any, input_color_space: str, output_color_space: str = "ACEScct") -> Any:
        """Transform an RGB float array to ACEScct (or another configured OCIO space).

        This reference implementation prioritises correctness/API portability. For
        image sequences use OCIO's PackedImageDesc/GPU processor in a batch worker.
        """
        try:
            import numpy as np
        except ImportError as exc:
            raise ColorManagementError("NumPy is required for pixel transforms") from exc
        array = np.asarray(pixels, dtype=np.float32)
        if array.shape[-1] < 3:
            raise ColorManagementError("Expected an RGB image with at least three channels")
        output = array.copy()
        cpu_processor = self.processor(input_color_space, output_color_space)
        for rgb in output[..., :3].reshape(-1, 3):
            value = [float(rgb[0]), float(rgb[1]), float(rgb[2])]
            cpu_processor.applyRGB(value)
            rgb[:] = value
        return output

    def bake_cube_lut(self, spec: OCIOTransformSpec) -> str:
        """Bake a 3D .cube LUT usable by FFmpeg and WebGL from the exact OCIO config."""
        if spec.cube_size not in {33, 65}:
            raise ColorManagementError("Broadcast OCIO LUTs must use cube size 33 or 65")
        ocio = _ocio()
        try:
            baker = ocio.Baker()
            baker.setConfig(self.config)
            baker.setFormat("iridas_cube")
            baker.setInputSpace(spec.input_color_space)
            baker.setTargetSpace(spec.output_color_space)
            baker.setCubeSize(spec.cube_size)
            return baker.bake()
        except Exception as exc:
            raise ColorManagementError("Unable to bake OCIO LUT; check colour-space names and config") from exc

    def bake_camera_to_acescct(self, camera_log: str, *, cube_size: int = 65) -> str:
        try:
            input_space = CAMERA_LOG_COLORSPACES[camera_log.lower()]
        except KeyError as exc:
            raise ColorManagementError(f"Unsupported camera log alias: {camera_log}") from exc
        return self.bake_cube_lut(OCIOTransformSpec(input_space, "ACEScct", cube_size))

    def bake_acescct_display_lut(self, display_color_space: str, *, cube_size: int = 65) -> str:
        """Bake ACEScct to a configured display transform, e.g. Rec.2100 PQ or HLG."""
        return self.bake_cube_lut(OCIOTransformSpec("ACEScct", display_color_space, cube_size))
