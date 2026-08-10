"""Beat-aware, non-destructive BGM shortening for a confirmed video timeline."""
from __future__ import annotations

import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.services.beat_sync import BeatSyncError, MusicStructure, analyze_music


class SmartAudioRemixError(RuntimeError):
    pass


@dataclass(frozen=True)
class MusicSection:
    role: str
    start: float
    end: float
    energy: float
    confidence: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _nearest_boundary(value: float, boundaries: list[float], *, upper: bool) -> float:
    if not boundaries:
        return value
    candidates = [item for item in boundaries if item >= value] if upper else [item for item in boundaries if item <= value]
    return (candidates[0] if upper and candidates else candidates[-1] if candidates else value)


def estimate_music_sections(audio_path: str | Path) -> tuple[MusicStructure, list[MusicSection], float]:
    """Estimate intro/verse/chorus/outro from beat bars and RMS energy.

    This is an explainable energy-based estimate, not a claim of perfect semantic
    song-section recognition. A specialist segmentation model can later replace it.
    """
    try:
        import librosa
        import numpy as np
    except ImportError as exc:
        raise SmartAudioRemixError("librosa and NumPy are required for Smart Audio Remix") from exc
    try:
        music = analyze_music(audio_path, detect_drops=True)
    except BeatSyncError as exc:
        raise SmartAudioRemixError(str(exc)) from exc
    samples, sample_rate = librosa.load(str(audio_path), sr=22050, mono=True)
    duration = float(librosa.get_duration(y=samples, sr=sample_rate))
    if duration < 2.0:
        raise SmartAudioRemixError("BGM must be at least two seconds long")
    hop = 512; rms = librosa.feature.rms(y=samples, hop_length=hop)[0]
    rms_times = librosa.frames_to_time(range(len(rms)), sr=sample_rate, hop_length=hop)
    boundaries = sorted({0.0, duration, *[float(item) for item in (music.downbeats or music.beats)]})
    # Four beats approximate a bar when downbeat estimation is not available.
    if len(boundaries) < 5 and music.beats:
        boundaries = [0.0, *[float(item) for item in music.beats[::4]], duration]
    intro_end = _nearest_boundary(min(duration * .22, max(3.0, 16 * 60 / max(music.tempo_bpm or 120, 1))), boundaries, upper=True)
    outro_start = _nearest_boundary(max(intro_end + 1.0, duration - max(3.0, 16 * 60 / max(music.tempo_bpm or 120, 1))), boundaries, upper=False)
    middle_start, middle_end = intro_end, max(intro_end + .2, outro_start)
    if middle_end <= middle_start:
        middle_start, middle_end = duration * .25, duration * .78
    energies: list[tuple[float, float, float]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        if end <= middle_start or start >= middle_end:
            continue
        mask = (rms_times >= start) & (rms_times < end)
        energies.append((start, end, float(np.mean(rms[mask])) if np.any(mask) else 0.0))
    if energies:
        peak_index = max(range(len(energies)), key=lambda index: energies[index][2])
        first, last = max(0, peak_index - 3), min(len(energies), peak_index + 5)
        chorus_start, chorus_end = energies[first][0], energies[last - 1][1]
        chorus_energy = float(np.mean([item[2] for item in energies[first:last]]))
    else:
        chorus_start, chorus_end, chorus_energy = middle_start, middle_end, 0.0
    max_energy = max((item[2] for item in energies), default=1.0) or 1.0
    sections = [
        MusicSection("intro", 0.0, max(.2, intro_end), 0.0, .65),
        MusicSection("verse", intro_end, max(intro_end + .1, chorus_start), 0.0, .45),
        MusicSection("chorus", chorus_start, max(chorus_start + .1, chorus_end), chorus_energy / max_energy, .72),
        MusicSection("verse", chorus_end, max(chorus_end + .1, outro_start), 0.0, .4),
        MusicSection("outro", outro_start, duration, 0.0, .68),
    ]
    return music, [item for item in sections if item.duration >= .1], duration


def _first_section(sections: list[MusicSection], role: str, fallback: MusicSection) -> MusicSection:
    return next((item for item in sections if item.role == role and item.duration >= .1), fallback)


def plan_smart_remix(*, sections: list[MusicSection], target_duration: float, bpm: float) -> dict[str, Any]:
    """Select intro + high-energy chorus windows + outro with crossfade-aware timing."""
    if target_duration < 1.0:
        raise SmartAudioRemixError("Target timeline must be at least one second")
    fallback = max(sections, key=lambda item: item.duration, default=None)
    if fallback is None:
        raise SmartAudioRemixError("No usable musical sections detected")
    intro, chorus, outro = _first_section(sections, "intro", fallback), _first_section(sections, "chorus", fallback), _first_section(sections, "outro", fallback)
    crossfade = min(.28, max(.08, 30 / max(bpm or 120, 1)))
    lead_duration = min(intro.duration, max(.7, target_duration * .18))
    tail_duration = min(outro.duration, max(.7, target_duration * .16))
    if target_duration <= lead_duration + tail_duration + .25:
        lead_duration, tail_duration = target_duration * .55, target_duration * .55
    # Solve the number of chorus windows because each added join loses a crossfade.
    middle_required = max(.2, target_duration - lead_duration - tail_duration + crossfade * 2)
    chorus_windows = max(1, math.ceil(middle_required / max(.4, chorus.duration)))
    while True:
        total_source = target_duration + crossfade * (chorus_windows + 1)
        middle_required = total_source - lead_duration - tail_duration
        next_count = max(1, math.ceil(middle_required / max(.4, chorus.duration)))
        if next_count == chorus_windows:
            break
        chorus_windows = next_count
    middle_duration = middle_required / chorus_windows
    segments = [
        {"role": "intro", "source_start": round(intro.start, 3), "source_end": round(min(intro.end, intro.start + lead_duration), 3)},
    ]
    for index in range(chorus_windows):
        available = max(.01, chorus.duration - middle_duration)
        offset = (index / max(1, chorus_windows - 1)) * available
        segments.append({"role": "chorus" if index == 0 else "chorus_loop", "source_start": round(chorus.start + offset, 3), "source_end": round(chorus.start + offset + middle_duration, 3)})
    segments.append({"role": "outro", "source_start": round(max(outro.start, outro.end - tail_duration), 3), "source_end": round(outro.end, 3)})
    return {"target_duration": round(target_duration, 3), "bpm": round(bpm, 2), "crossfade_seconds": round(crossfade, 3), "segments": segments}


def build_remix_command(*, input_path: str, plan: dict[str, Any], output_path: str) -> list[str]:
    segments = list(plan.get("segments", [])); target = float(plan.get("target_duration", 0)); crossfade = float(plan.get("crossfade_seconds", .16))
    if len(segments) < 1 or target <= 0:
        raise SmartAudioRemixError("Invalid remix plan")
    filters: list[str] = []
    for index, segment in enumerate(segments):
        start, end = float(segment["source_start"]), float(segment["source_end"])
        if end <= start:
            raise SmartAudioRemixError("Remix segment end must be after start")
        filters.append(f"[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[a{index}]")
    current = "a0"
    for index in range(1, len(segments)):
        next_label = f"mix{index}"
        filters.append(f"[{current}][a{index}]acrossfade=d={crossfade:.6f}:c1=tri:c2=tri[{next_label}]")
        current = next_label
    fade_duration = min(.55, max(.12, target * .08)); fade_start = max(0, target - fade_duration)
    filters.append(f"[{current}]atrim=duration={target:.6f},afade=t=out:st={fade_start:.6f}:d={fade_duration:.6f},aresample=48000[remix]")
    return ["ffmpeg", "-y", "-i", input_path, "-filter_complex", ";".join(filters), "-map", "[remix]", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output_path]


def serialise_sections(sections: list[MusicSection]) -> list[dict[str, Any]]:
    return [asdict(item) for item in sections]

