"""MV-HEVC encode/mux orchestration with a mandatory Apple metadata verification gate."""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import settings


class MVHEVCPackagingError(RuntimeError):
    pass


def _run(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout or settings.spatial_mvhevc_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise MVHEVCPackagingError("MV-HEVC command timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise MVHEVCPackagingError((exc.stderr or "MV-HEVC command failed")[-4000:]) from exc
    except OSError as exc:
        raise MVHEVCPackagingError("Custom spatial FFmpeg is not installed or executable") from exc


def _format_external_command(template: str | None, **values: str) -> list[str]:
    if not template:
        raise MVHEVCPackagingError("Apple spatial metadata writer/verifier is not configured")
    required = {"{input}", "{output}", "{metadata}"}
    tokens = shlex.split(template)
    if not required.issubset(set(tokens)):
        raise MVHEVCPackagingError("Spatial metadata command must contain {input}, {output}, and {metadata} as standalone arguments")
    # Substitute after tokenisation so object keys containing spaces never become extra shell arguments.
    return [values.get(token.strip("{}"), token) if token in required else token for token in tokens]


def assert_mvhevc_capability() -> dict[str, str]:
    """Refuse a standard HEVC encoder: it produces two-dimensional video, not MV-HEVC."""
    encoder_help = _run([settings.spatial_ffmpeg_path, "-hide_banner", "-h", "encoder=hevc_nvenc"], timeout=30).stdout.lower()
    filters = _run([settings.spatial_ffmpeg_path, "-hide_banner", "-filters"], timeout=30).stdout.lower()
    if "multiview" not in encoder_help or "framepack" not in filters:
        raise MVHEVCPackagingError("Spatial FFmpeg lacks hevc_nvenc Multiview Main and framepack support; install the custom MV-HEVC build")
    return {"ffmpeg": settings.spatial_ffmpeg_path, "encoder": "hevc_nvenc", "profile": "multiview_main"}


def _audio_stream_info(media_path: str | Path) -> dict[str, Any]:
    probe = _run([
        settings.spatial_ffprobe_path, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,channels,channel_layout", "-of", "json", str(media_path),
    ], timeout=60)
    streams = json.loads(probe.stdout).get("streams", [])
    if not streams:
        raise MVHEVCPackagingError("Source render has no audio track")
    stream = dict(streams[0])
    if int(stream.get("channels") or 0) != 12:
        raise MVHEVCPackagingError("Spatial Video export requires the Phase 13 7.1.4 (12-channel) spatial-audio master")
    return stream


def mux_mvhevc_with_spatial_audio(
    left_path: str | Path,
    right_path: str | Path,
    spatial_audio_source: str | Path,
    intermediate_mov: str | Path,
) -> dict[str, Any]:
    """Use frame-sequence side data to feed a patched NVENC MV-HEVC encoder."""
    capability = assert_mvhevc_capability()
    audio = _audio_stream_info(spatial_audio_source)
    command = [
        settings.spatial_ffmpeg_path, "-y", "-i", str(left_path), "-i", str(right_path), "-i", str(spatial_audio_source),
        "-filter_complex", "[0:v][1:v]framepack=format=frameseq[stereo]",
        "-map", "[stereo]", "-map", "2:a:0", "-c:v", "hevc_nvenc", "-profile:v", "multiview_main",
        "-preset", "p6", "-rc", "vbr", "-cq", "22", "-b:v", "30M", "-maxrate", "60M",
        "-tag:v", "hvc1", "-c:a", "copy", "-movflags", "+faststart", "-shortest", str(intermediate_mov),
    ]
    _run(command)
    return {"capability": capability, "audio": audio, "encode_command": command}


def _verify_hevc_and_audio(path: str | Path) -> dict[str, Any]:
    probe = _run([
        settings.spatial_ffprobe_path, "-v", "error", "-show_entries",
        "stream=index,codec_type,codec_name,codec_tag_string,profile,channels,channel_layout:format=format_name",
        "-of", "json", str(path),
    ], timeout=60)
    payload = json.loads(probe.stdout)
    video = next((stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"), None)
    if not video or video.get("codec_name") != "hevc" or video.get("codec_tag_string") != "hvc1":
        raise MVHEVCPackagingError("Output is not an hvc1 HEVC QuickTime stream")
    if not audio or int(audio.get("channels") or 0) != 12:
        raise MVHEVCPackagingError("Output did not preserve the 7.1.4 channel bed")
    return {"ffprobe": payload, "video_profile": video.get("profile"), "audio_channels": int(audio["channels"])}


def attach_and_verify_apple_spatial_metadata(intermediate_mov: str | Path, output_mov: str | Path, metadata: dict[str, Any], workdir: str | Path) -> dict[str, Any]:
    """Delegate VEXU/spatial metadata writing to a macOS AVFoundation worker, then verify it.

    FFmpeg can create the MV-HEVC elementary stream but must not pretend to author
    Apple's evolving spatial metadata boxes. The external writer is intentionally
    mandatory and should be built from Apple's AVFoundation spatial-video sample.
    """
    metadata_path = Path(workdir) / "apple-spatial-metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    _run(_format_external_command(settings.spatial_metadata_writer_command, input=str(intermediate_mov), output=str(output_mov), metadata=str(metadata_path)))
    if not Path(output_mov).is_file():
        raise MVHEVCPackagingError("Apple metadata writer did not create a MOV output")
    basic = _verify_hevc_and_audio(output_mov)
    # A platform verifier must inspect VEXU, layer IDs/eye tags, baseline, FOV and disparity metadata.
    _run(_format_external_command(settings.spatial_metadata_verifier_command, input=str(output_mov), output=str(output_mov), metadata=str(metadata_path)), timeout=120)
    return {**basic, "apple_metadata": metadata, "metadata_writer": "external_avfoundation", "verified": True}
