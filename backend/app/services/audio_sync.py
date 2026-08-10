"""FFT cross-correlation for external recorder / camera audio synchronization."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class AudioSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioOffset:
    # Timeline position of external-audio t=0 relative to camera-video t=0.
    offset_seconds: float
    confidence: float
    sample_rate: int


def extract_sync_wav(input_path: str | Path, output_path: str | Path) -> None:
    try:
        subprocess.run(["ffmpeg", "-y", "-i", str(input_path), "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output_path)], check=True, capture_output=True, text=True, timeout=20 * 60)
    except subprocess.TimeoutExpired as exc:
        raise AudioSyncError("Audio extraction for synchronization timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise AudioSyncError((exc.stderr or "Could not extract audio for synchronization")[-1600:]) from exc


def _envelope(samples, sample_rate: int):
    import numpy as np
    frame = max(1, round(sample_rate * .02)); usable = len(samples) - len(samples) % frame
    if usable < frame * 8: raise AudioSyncError("Audio is too short to synchronize")
    values = samples[:usable].reshape(-1, frame)
    envelope = np.sqrt(np.mean(values * values, axis=1))
    envelope = np.diff(envelope, prepend=envelope[0])  # suppress steady noise; retain speech onsets.
    envelope -= envelope.mean(); standard_deviation = envelope.std()
    if standard_deviation < 1e-7: raise AudioSyncError("Audio has insufficient changing acoustic content for synchronization")
    return envelope / standard_deviation, sample_rate / frame


def estimate_audio_offset(camera_wav: str | Path, external_wav: str | Path, *, max_offset_seconds: float = 120.0) -> AudioOffset:
    """Return where external audio begins on the camera timeline using FFT correlation.

    Positive offset means the external recorder began later, so its source t=0
    must be placed later on the Timeline. The signal uses 20 ms RMS-onset bins.
    """
    try:
        import numpy as np
        from scipy.io import wavfile
        from scipy.signal import correlate, correlation_lags
    except ImportError as exc:
        raise AudioSyncError("SciPy and NumPy are required for audio synchronization") from exc
    camera_rate, camera = wavfile.read(str(camera_wav)); external_rate, external = wavfile.read(str(external_wav))
    if camera_rate != external_rate: raise AudioSyncError("Normalized sync WAV files must have the same sample rate")
    camera = np.asarray(camera, dtype=np.float32).reshape(-1); external = np.asarray(external, dtype=np.float32).reshape(-1)
    camera_envelope, bins_per_second = _envelope(camera, int(camera_rate)); external_envelope, _ = _envelope(external, int(external_rate))
    max_lag = int(max_offset_seconds * bins_per_second)
    correlation = correlate(external_envelope, camera_envelope, mode="full", method="fft"); lags = correlation_lags(len(external_envelope), len(camera_envelope), mode="full")
    valid = (lags >= -max_lag) & (lags <= max_lag)
    if not valid.any(): raise AudioSyncError("No valid offset window for synchronization")
    values, valid_lags = correlation[valid], lags[valid]; best_index = int(np.argmax(values)); lag = int(valid_lags[best_index])
    # For correlate(external, camera), a negative lag means external content occurs earlier in its
    # local file, i.e. the external recording started later in wall-clock / video time.
    offset = -lag / bins_per_second
    baseline = float(np.median(np.abs(values))) + 1e-8; confidence = max(0.0, min(1.0, float(values[best_index]) / (baseline * 12)))
    if confidence < .18: raise AudioSyncError("Audio correlation confidence is too low; choose clips with shared speech or a clap")
    return AudioOffset(offset_seconds=round(offset, 3), confidence=round(confidence, 3), sample_rate=int(camera_rate))


def build_synced_audio_replace_command(*, video_path: str, external_audio_path: str, output_path: str, offset_seconds: float, timeline_segments: list[dict[str, float]]) -> list[str]:
    """Replace noisy camera audio and preserve sync through every kept jump-cut segment."""
    filters: list[str] = []; labels: list[str] = []
    for index, segment in enumerate(timeline_segments):
        video_start, video_end = float(segment["source_start"]), float(segment["source_end"]); duration = video_end - video_start
        if duration <= 0: continue
        external_start, external_end = video_start - offset_seconds, video_end - offset_seconds; label = f"sync{index}"
        if external_end <= 0:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.6f}[{label}]")
        elif external_start < 0:
            silence = min(duration, -external_start); media = max(0.0, duration - silence)
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={silence:.6f}[syncsilence{index}]")
            filters.append(f"[1:a]aresample=48000,atrim=start=0:end={media:.6f},asetpts=PTS-STARTPTS,apad,atrim=duration={media:.6f}[syncmedia{index}]")
            filters.append(f"[syncsilence{index}][syncmedia{index}]concat=n=2:v=0:a=1[{label}]")
        else:
            filters.append(f"[1:a]aresample=48000,atrim=start={external_start:.6f}:end={external_end:.6f},asetpts=PTS-STARTPTS,apad,atrim=duration={duration:.6f}[{label}]")
        labels.append(label)
    if not labels: raise AudioSyncError("No kept video segments are available for synchronized audio")
    filters.append(f"{''.join(f'[{label}]' for label in labels)}concat=n={len(labels)}:v=0:a=1[synced]")
    return ["ffmpeg", "-y", "-i", video_path, "-i", external_audio_path, "-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[synced]", "-c:v", "copy", "-c:a", "aac", "-shortest", output_path]
