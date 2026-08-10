"""Conservative, export-safe beauty and image-enhancement filter settings."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BeautyEnhancement:
    enabled: bool = True
    skin_smoothing: int = 35
    brightness: int = 8
    contrast: int = 10
    denoise: int = 30
    sharpen: int = 25

    @classmethod
    def from_json(cls, value: object) -> "BeautyEnhancement":
        if not isinstance(value, dict):
            return cls(enabled=False, skin_smoothing=0, brightness=0, contrast=0, denoise=0, sharpen=0)

        def bounded(key: str, default: int) -> int:
            try:
                return max(0, min(100, int(value.get(key, default))))
            except (TypeError, ValueError):
                return default

        return cls(
            enabled=bool(value.get("enabled", True)),
            skin_smoothing=bounded("skin_smoothing", 35),
            brightness=bounded("brightness", 8),
            contrast=bounded("contrast", 10),
            denoise=bounded("denoise", 30),
            sharpen=bounded("sharpen", 25),
        )

    def as_json(self) -> dict[str, int | bool]:
        return {
            "enabled": self.enabled,
            "skin_smoothing": self.skin_smoothing,
            "brightness": self.brightness,
            "contrast": self.contrast,
            "denoise": self.denoise,
            "sharpen": self.sharpen,
        }

    def ffmpeg_filters(self) -> list[str]:
        """Return modest filters; values are clamped before reaching FFmpeg expressions."""
        if not self.enabled:
            return []
        filters: list[str] = []
        # hqdn3d gives low-light chroma/luma cleanup. Smoothing adds only a small amount so
        # it remains a natural-looking whole-frame fallback when a face mask is unavailable.
        noise_strength = (self.denoise / 100) * 2.2 + (self.skin_smoothing / 100) * 0.8
        if noise_strength > 0.03:
            chroma = noise_strength * 0.75
            filters.append(f"hqdn3d={noise_strength:.3f}:{chroma:.3f}:{noise_strength * 2.5:.3f}:{chroma * 2.5:.3f}")
        if self.sharpen:
            amount = 0.15 + (self.sharpen / 100) * 0.85
            filters.append(f"unsharp=5:5:{amount:.3f}:5:5:0.000")
        if self.brightness or self.contrast:
            # FFmpeg eq brightness range is -1..1; keep consumer presets intentionally subtle.
            brightness = self.brightness / 1000
            contrast = 1 + self.contrast / 250
            filters.append(f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}")
        return filters
