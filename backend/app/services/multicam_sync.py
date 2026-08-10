"""FFT audio synchronization and multi-camera timeline construction."""
from __future__ import annotations

import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SYNC_SAMPLE_RATE = 8_000


class MulticamSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class CameraSource:
    asset_id: str
    path: str
    duration_seconds: float
    label: str


@dataclass(frozen=True)
class SyncOffset:
    asset_id: str
    offset_seconds: float
    confidence: float
    label: str


def extract_sync_audio(video_path: str | Path, *, sample_rate: int = SYNC_SAMPLE_RATE, max_seconds: int = 900):
    """Read a bounded mono PCM analysis window without writing a large WAV to disk."""
    try:
        import numpy as np
    except ImportError as exc:
        raise MulticamSyncError("NumPy is required for multicam synchronization") from exc
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(video_path), "-map", "0:a:0", "-t", str(max_seconds),
                "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1",
            ],
            check=True, capture_output=True, timeout=max_seconds + 120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise MulticamSyncError(f"Unable to extract sync audio from {video_path}") from exc
    samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)
    if samples.size < sample_rate * 3:
        raise MulticamSyncError("Audio analysis window is too short for reliable synchronization")
    samples -= samples.mean()
    deviation = samples.std()
    if deviation < 1e-4:
        raise MulticamSyncError("Audio track is silent; cannot synchronize cameras")
    return samples / deviation


def estimate_audio_offset(
    reference_samples,
    candidate_samples,
    *,
    sample_rate: int = SYNC_SAMPLE_RATE,
    max_offset_seconds: float = 120.0,
) -> tuple[float, float]:
    """Return candidate-minus-reference offset; positive means candidate starts later.

    FFT correlation is restricted to plausible lags. A parabolic peak interpolation
    supplies sub-sample precision without increasing the input sample rate.
    """
    try:
        import numpy as np
        from scipy.signal import correlate, correlation_lags
    except ImportError as exc:
        raise MulticamSyncError("SciPy and NumPy are required for FFT cross-correlation") from exc
    correlation = correlate(candidate_samples, reference_samples, mode="full", method="fft")
    lags = correlation_lags(candidate_samples.size, reference_samples.size, mode="full")
    max_lag = int(max_offset_seconds * sample_rate)
    valid = (lags >= -max_lag) & (lags <= max_lag)
    correlation, lags = correlation[valid], lags[valid]
    peak_index = int(np.argmax(correlation))
    fractional_lag = 0.0
    if 0 < peak_index < len(correlation) - 1:
        left, centre, right = correlation[peak_index - 1 : peak_index + 2]
        denominator = left - 2 * centre + right
        if abs(denominator) > 1e-12:
            fractional_lag = float(0.5 * (left - right) / denominator)
    lag_samples = float(lags[peak_index]) + fractional_lag
    # Inputs are z-normalised, so this is an approximate correlation coefficient.
    confidence = min(1.0, max(0.0, float(correlation[peak_index]) / min(reference_samples.size, candidate_samples.size)))
    return lag_samples / sample_rate, confidence


def synchronize_cameras(
    reference: CameraSource,
    cameras: list[CameraSource],
    *,
    max_offset_seconds: float = 120.0,
    analysis_seconds: int = 900,
) -> list[SyncOffset]:
    reference_audio = extract_sync_audio(reference.path, max_seconds=analysis_seconds)
    results = [SyncOffset(reference.asset_id, 0.0, 1.0, reference.label)]
    for camera in cameras:
        candidate_audio = extract_sync_audio(camera.path, max_seconds=analysis_seconds)
        offset, confidence = estimate_audio_offset(reference_audio, candidate_audio, max_offset_seconds=max_offset_seconds)
        results.append(SyncOffset(camera.asset_id, offset, confidence, camera.label))
    return results


def build_multicam_timeline(
    reference: CameraSource,
    cameras: list[CameraSource],
    offsets: list[SyncOffset],
) -> dict[str, Any]:
    """Put aligned angles on dedicated tracks; only the reference track carries audio."""
    offset_by_asset = {item.asset_id: item for item in offsets}
    tracks: list[dict[str, Any]] = [{
        "type": "main_video", "z_index": 0, "camera_label": reference.label,
        "clips": [{
            "source_asset_id": reference.asset_id, "source_start": 0.0,
            "source_end": reference.duration_seconds, "timeline_start": 0.0,
            "action": "keep", "z_index": 0, "audio_enabled": True,
        }],
    }]
    for index, camera in enumerate(cameras, start=1):
        offset = offset_by_asset[camera.asset_id].offset_seconds
        timeline_start = max(0.0, -offset)
        source_start = max(0.0, offset)
        overlap = min(reference.duration_seconds - timeline_start, camera.duration_seconds - source_start)
        if overlap <= 0:
            continue
        tracks.append({
            "type": "multicam_video", "z_index": index, "camera_label": camera.label,
            "sync": asdict(offset_by_asset[camera.asset_id]),
            "clips": [{
                "source_asset_id": camera.asset_id, "source_start": round(source_start, 4),
                "source_end": round(source_start + overlap, 4), "timeline_start": round(timeline_start, 4),
                "action": "keep", "z_index": index, "audio_enabled": False,
            }],
        })
    return {"version": 1, "kind": "multicam_sync", "reference_asset_id": reference.asset_id, "tracks": tracks, "offsets": [asdict(item) for item in offsets]}


def speaker_energy_windows_from_audio(audio_path: str | Path, *, window_ms: int = 500) -> list[tuple[float, float]]:
    """Produce timestamped dBFS windows suitable for automatic angle switching."""
    try:
        from pydub import AudioSegment
    except ImportError as exc:
        raise MulticamSyncError("pydub is required for automatic multicam switching") from exc
    audio = AudioSegment.from_file(str(audio_path))
    windows: list[tuple[float, float]] = []
    for offset in range(0, len(audio), window_ms):
        level = audio[offset : offset + window_ms].dBFS
        windows.append(((offset + window_ms / 2) / 1000, -90.0 if not math.isfinite(level) else level))
    return windows


def auto_switch_multicam(
    speaker_energy_windows: list[tuple[float, float]],
    *,
    main_asset_id: str,
    closeup_asset_id: str,
    closeup_offset_seconds: float,
    duration_seconds: float,
    min_shot_seconds: float = 2.5,
) -> list[dict[str, Any]]:
    """Choose close-up during energetic speech, otherwise main camera; keep cuts readable."""
    if not speaker_energy_windows:
        return []
    energies = [energy for _, energy in speaker_energy_windows]
    baseline = sum(energies) / len(energies)
    threshold = baseline + max(3.0, math.sqrt(sum((value - baseline) ** 2 for value in energies) / len(energies)))
    choices: list[tuple[float, str]] = []
    for timestamp, energy in speaker_energy_windows:
        closeup_available = 0 <= timestamp + closeup_offset_seconds
        choices.append((timestamp, closeup_asset_id if closeup_available and energy >= threshold else main_asset_id))
    segments: list[dict[str, Any]] = []
    start, selected = choices[0]
    for timestamp, camera_id in choices[1:]:
        if camera_id == selected or timestamp - start < min_shot_seconds:
            continue
        segments.append({"timeline_start": start, "timeline_end": timestamp, "source_asset_id": selected})
        start, selected = timestamp, camera_id
    segments.append({"timeline_start": start, "timeline_end": duration_seconds, "source_asset_id": selected})
    for segment in segments:
        offset = closeup_offset_seconds if segment["source_asset_id"] == closeup_asset_id else 0.0
        segment["source_start"] = max(0.0, segment["timeline_start"] + offset)
        segment["source_end"] = max(segment["source_start"], segment["timeline_end"] + offset)
        segment["reason"] = "energetic_speech" if segment["source_asset_id"] == closeup_asset_id else "default_main_angle"
    return segments


def build_multicam_switch_filtergraph(
    switches: list[dict[str, Any]],
    *,
    source_input_indexes: dict[str, int],
    audio_input_index: int = 0,
) -> str:
    """Create FFmpeg trim/concat filters for auto-cut camera switching.

    Video uses each selected camera's synchronized source time, while audio always
    remains on the reference camera input so switching never causes audio jumps.
    """
    if not switches:
        raise MulticamSyncError("At least one camera switch is required")
    filters: list[str] = []
    concat_inputs: list[str] = []
    for index, switch in enumerate(switches):
        asset_id = str(switch["source_asset_id"])
        input_index = source_input_indexes.get(asset_id)
        if input_index is None:
            raise MulticamSyncError(f"No FFmpeg input index for camera asset {asset_id}")
        timeline_start = float(switch["timeline_start"])
        timeline_end = float(switch["timeline_end"])
        source_start = float(switch["source_start"])
        source_end = float(switch["source_end"])
        if timeline_end <= timeline_start or source_end <= source_start:
            raise MulticamSyncError("Invalid multicam switch duration")
        video_label, audio_label = f"mcv{index}", f"mca{index}"
        filters.append(
            f"[{input_index}:v]trim=start={source_start:.6f}:end={source_end:.6f},setpts=PTS-STARTPTS[{video_label}]"
        )
        filters.append(
            f"[{audio_input_index}:a]atrim=start={timeline_start:.6f}:end={timeline_end:.6f},asetpts=PTS-STARTPTS[{audio_label}]"
        )
        concat_inputs.extend([f"[{video_label}]", f"[{audio_label}]"])
    filters.append(f"{''.join(concat_inputs)}concat=n={len(switches)}:v=1:a=1[outv][outa]")
    return ";".join(filters)
