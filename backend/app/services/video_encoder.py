"""Runtime-safe FFmpeg encoder selection for heterogeneous CPU/GPU render workers."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import subprocess


@dataclass(frozen=True)
class VideoEncoderSettings:
    codec: str
    preset: str
    extra_args: tuple[str, ...]


def _command_succeeds(command: list[str]) -> bool:
    try:
        return subprocess.run(command, capture_output=True, timeout=3, check=False).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@lru_cache(maxsize=1)
def nvidia_gpu_available() -> bool:
    """A GPU device alone is insufficient: it must be visible to the container."""
    return _command_succeeds(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])


@lru_cache(maxsize=1)
def ffmpeg_encoders() -> str:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return f"{result.stdout}\n{result.stderr}"


def _nvenc_available(codec: str) -> bool:
    return nvidia_gpu_available() and codec in ffmpeg_encoders()


def resolve_video_encoder(preference: str = "auto") -> VideoEncoderSettings:
    """Prefer NVENC but always return a usable CPU fallback.

    ``preference`` supports ``auto``, ``h264``, ``hevc`` or an explicit FFmpeg
    encoder such as ``libx264``.  NVENC p6 is a high-quality throughput preset.
    """
    normalized = preference.lower()
    requested_hevc = normalized in {"hevc", "hevc_nvenc", "libx265"}
    nvenc_codec = "hevc_nvenc" if requested_hevc else "h264_nvenc"
    if normalized in {"auto", "h264", "hevc", "h264_nvenc", "hevc_nvenc"} and _nvenc_available(nvenc_codec):
        return VideoEncoderSettings(
            codec=nvenc_codec,
            preset="p6",
            extra_args=("-rc", "vbr", "-cq", "19", "-spatial_aq", "1", "-aq-strength", "8"),
        )
    if normalized in {"auto", "h264", "h264_nvenc"}:
        return VideoEncoderSettings(codec="libx264", preset="fast", extra_args=("-crf", "20"))
    if normalized in {"hevc", "hevc_nvenc", "libx265"}:
        return VideoEncoderSettings(codec="libx265", preset="fast", extra_args=("-crf", "24"))
    return VideoEncoderSettings(codec=preference, preset="fast", extra_args=())
