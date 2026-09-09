"""FFmpeg operations shared by media ingestion and its real-file tests."""

import json
import subprocess
from fractions import Fraction
from pathlib import Path

FFMPEG_TIMEOUT_SECONDS = 15 * 60


class MediaProcessingError(RuntimeError):
    pass


def run(command: list[str], timeout: int = FFMPEG_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaProcessingError(f"Command timed out after {timeout}s: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()[-2000:]
        raise MediaProcessingError(f"FFmpeg command failed: {detail}") from exc
    except OSError as exc:
        raise MediaProcessingError("ffmpeg/ffprobe is not installed or not executable") from exc


def probe(input_path: Path) -> dict[str, object]:
    result = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
        "-of", "json", str(input_path),
    ], timeout=120)
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    try:
        fps = float(Fraction(str(video.get("avg_frame_rate") or "0/0")))
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "duration": float((payload.get("format") or {}).get("duration") or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "video_codec": video.get("codec_name"),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
    }


def extract_audio(input_path: Path, output_path: Path, *, has_audio: bool) -> bool:
    """Return whether a WAV was produced; a silent source has no audio artifact.

    Do not treat arbitrary FFmpeg failures as silence. A source that advertises
    audio must still fail ingestion if its stream cannot be decoded.
    """
    if not has_audio:
        return False
    run([
        "ffmpeg", "-y", "-i", str(input_path), "-vn", "-map", "0:a:0",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output_path),
    ])
    return True
