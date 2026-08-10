"""Music-structure analysis, visual-momentum matching, and optical-flow speed-ramp plans."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import random


class BeatSyncError(RuntimeError): pass


@dataclass(frozen=True)
class MusicStructure:
    tempo_bpm: float
    onsets: list[float]
    beats: list[float]
    downbeats: list[float]
    drops: list[float]
    energy_peaks: list[float]


@dataclass(frozen=True)
class VisualMomentumEvent:
    time: float
    score: float
    motion: float
    luminance_change: float
    transition_bonus: float = 0.0


def analyze_music(audio_path: str | Path, *, detect_drops: bool = True) -> MusicStructure:
    try:
        import librosa
        import numpy as np
    except ImportError as exc: raise BeatSyncError("Install librosa on the worker to analyze BGM structure") from exc
    samples, sample_rate = librosa.load(str(audio_path), sr=22050, mono=True)
    if len(samples) == 0: raise BeatSyncError("BGM audio is empty")
    hop = 512; onset_env = librosa.onset.onset_strength(y=samples, sr=sample_rate, hop_length=hop, aggregate=np.median)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sample_rate, hop_length=hop, backtrack=True)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sample_rate, hop_length=hop)
    beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate, hop_length=hop).tolist(); onset_times = librosa.frames_to_time(onset_frames, sr=sample_rate, hop_length=hop).tolist()
    rms = librosa.feature.rms(y=samples, hop_length=hop)[0]; rms_times = librosa.frames_to_time(range(len(rms)), sr=sample_rate, hop_length=hop)
    threshold = float(np.percentile(rms, 88)); energy_peaks = [float(rms_times[index]) for index in range(1, len(rms)-1) if rms[index] >= threshold and rms[index] >= rms[index-1] and rms[index] >= rms[index+1]]
    # Without meter classification, downbeats are the beat phase with the strongest average onset energy.
    if beat_frames.size:
        phases = [float(np.mean([onset_env[frame] for frame in beat_frames[phase::4] if frame < len(onset_env)] or [0])) for phase in range(4)]
        downbeats = [float(beat_times[index]) for index in range(max(range(4), key=lambda phase: phases[phase]), len(beat_times), 4)]
    else: downbeats = []
    drops = []
    if detect_drops:
        median = float(np.median(rms)); drops = [time for time in energy_peaks if rms[min(len(rms)-1, int(time*sample_rate/hop))] > median * 1.9]
    return MusicStructure(float(np.asarray(tempo).flat[0] if np.asarray(tempo).size else 0), [float(item) for item in onset_times], [float(item) for item in beat_times], downbeats, drops, energy_peaks)


def analyze_visual_momentum(video_path: str | Path, *, sample_fps: float = 8.0, transition_times: list[float] | None = None) -> list[VisualMomentumEvent]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc: raise BeatSyncError("OpenCV and NumPy are required for visual momentum") from exc
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened(): raise BeatSyncError("Cannot decode source video for visual momentum")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.; stride = max(1, round(fps/sample_fps)); previous = None; index = 0; events: list[VisualMomentumEvent] = []; transition_times = transition_times or []
    try:
        while True:
            ok, frame = capture.read()
            if not ok: break
            if index % stride: index += 1; continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if previous is not None:
                flow = cv2.calcOpticalFlowFarneback(previous, gray, None, .5, 3, 15, 3, 5, 1.2, 0); motion = float(np.mean(np.linalg.norm(flow, axis=2)))
                luminance = float(np.mean(np.abs(gray.astype(np.float32) - previous.astype(np.float32))) / 255)
                time = index / fps; bonus = .5 if any(abs(time - value) < .18 for value in transition_times) else 0
                events.append(VisualMomentumEvent(time, motion * .7 + luminance * 4 + bonus, motion, luminance, bonus))
            previous = gray; index += 1
    finally: capture.release()
    if not events: return []
    values = np.asarray([item.score for item in events]); threshold = float(np.percentile(values, 82))
    return [item for position, item in enumerate(events) if item.score >= threshold and (position == 0 or item.score >= events[position-1].score) and (position == len(events)-1 or item.score >= events[position+1].score)]


def align_visual_events(events: list[VisualMomentumEvent], music: MusicStructure, *, max_matches: int = 24) -> list[dict[str, Any]]:
    """Globally favor strong moments and structural beats, while preventing multiple cuts on one beat."""
    targets = [(time, "drop", 1.0) for time in music.drops] + [(time, "downbeat", .82) for time in music.downbeats] + [(time, "beat", .55) for time in music.beats]
    used: set[int] = set(); matches: list[dict[str, Any]] = []
    max_score = max((event.score for event in events), default=1.0)
    for event in sorted(events, key=lambda item: item.score, reverse=True):
        candidates = [(abs(event.time-time) / max(.12, 1/((music.tempo_bpm or 120)/60)) - weight, index, time, kind) for index, (time, kind, weight) in enumerate(targets) if index not in used]
        if not candidates: break
        _, index, beat, kind = min(candidates); used.add(index)
        confidence = min(1.0, 0.35 + (event.score / max_score) * 0.45 + (0.15 if kind == "drop" else 0.05 if kind == "downbeat" else 0.0))
        matches.append({"visual_time": round(event.time, 3), "music_time": round(beat, 3), "target": kind, "confidence": round(confidence, 3), "reason": f"visual momentum {event.score:.2f} aligned to {kind}"})
        if len(matches) >= max_matches: break
    return sorted(matches, key=lambda item: item["music_time"])


def speed_ramp_plan(start: float, end: float, *, peak_speed: float = 1.28, trough_speed: float = .72) -> dict[str, Any]:
    if end <= start or not .25 <= trough_speed <= 1 <= peak_speed <= 2: raise BeatSyncError("Invalid speed-ramp range")
    duration = end-start
    boundaries = [start, start + duration * .25, start + duration * .75, end]
    return {
        "start_time": start,
        "end_time": end,
        "curve": "fast_slow_fast",
        "peak_speed": peak_speed,
        "trough_speed": trough_speed,
        # Piecewise remapping is deterministic and can be rendered in the filtergraph.
        # The middle section receives the optical-flow interpolation pass.
        "sections": [
            {"source_start": boundaries[0], "source_end": boundaries[1], "speed": peak_speed},
            {"source_start": boundaries[1], "source_end": boundaries[2], "speed": trough_speed},
            {"source_start": boundaries[2], "source_end": boundaries[3], "speed": peak_speed},
        ],
        "interpolation": "minterpolate=fps=TARGET_FPS:mi_mode=mci:mc_mode=aobmc:me_mode=bidir",
    }


def build_speed_ramp_filter(
    input_label: str,
    ramp: dict[str, Any],
    output_label: str,
    *,
    target_fps: float,
) -> list[str]:
    """Create a video-only FFmpeg graph for one plan returned by :func:`speed_ramp_plan`.

    The caller can place this after the master-video concat. BGM remains clock-stable; if
    production requires original location audio to follow the ramp, render/mix it separately
    with matching piecewise ``atempo`` sections.
    """
    if target_fps <= 0:
        raise BeatSyncError("target_fps must be positive")
    raw_sections = ramp.get("sections")
    if not isinstance(raw_sections, list) or len(raw_sections) != 3:
        raise BeatSyncError("Speed-ramp plan must contain three sections")
    labels: list[str] = []
    filters: list[str] = []
    for index, section in enumerate(raw_sections):
        try:
            start, end, speed = float(section["source_start"]), float(section["source_end"]), float(section["speed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BeatSyncError("Invalid speed-ramp section") from exc
        if end <= start or not .25 <= speed <= 2:
            raise BeatSyncError("Invalid speed-ramp section range or speed")
        label = f"{output_label}part{index}"
        labels.append(label)
        filters.append(
            f"[{input_label}]trim=start={start:.6f}:end={end:.6f},setpts=(PTS-STARTPTS)/{speed:.6f},"
            f"minterpolate=fps={target_fps:.3f}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir[{label}]"
        )
    filters.append(f"{''.join(f'[{label}]' for label in labels)}concat=n=3:v=1:a=0[{output_label}]")
    return filters


def beat_sync_report(bgm_path: str | Path, video_path: str | Path, *, transition_times: list[float] | None = None, max_matches: int = 24, detect_drops: bool = True) -> dict[str, Any]:
    music = analyze_music(bgm_path, detect_drops=detect_drops); visual = analyze_visual_momentum(video_path, transition_times=transition_times); matches = align_visual_events(visual, music, max_matches=max_matches)
    ramps = [speed_ramp_plan(max(0., item["visual_time"]-.25), item["visual_time"]+.25) for item in matches if item["target"] in {"drop", "downbeat"}]
    return {"music": asdict(music), "visual_momentum": [asdict(item) for item in visual], "matches": matches, "speed_ramps": ramps}


def select_dynamic_window(video_path: str | Path, *, duration_seconds: float, asset_duration: float) -> tuple[float, float, float]:
    """Choose one short, high-motion source window; returns in/out and explainable score."""
    events = analyze_visual_momentum(video_path)
    best = max(events, key=lambda item: item.score) if events else VisualMomentumEvent(asset_duration / 2, .1, 0, 0)
    duration = min(max(.25, duration_seconds), max(.25, asset_duration))
    start = min(max(0., best.time - duration / 2), max(0., asset_duration - duration))
    return round(start, 3), round(start + duration, 3), round(best.score, 4)


def build_beat_montage_document(*, music: MusicStructure, candidates: list[dict[str, Any]], aspect_ratio: str) -> dict[str, Any]:
    """Fill one clip per beat interval and decorate drop/cut boundaries with deterministic impact effects."""
    if len(candidates) < 1 or len(music.beats) < 2:
        raise BeatSyncError("BGM must contain at least two detectable beats and one usable source")
    count = min(len(candidates), len(music.beats) - 1)
    clips: list[dict[str, Any]] = []; effects: list[dict[str, Any]] = []
    timeline_origin = float(music.beats[0])
    for index in range(count):
        start, end = float(music.beats[index] - timeline_origin), float(music.beats[index + 1] - timeline_origin)
        duration = end - start
        if duration < .20: continue
        candidate = candidates[index]
        source_start = min(float(candidate["source_start"]), max(0., float(candidate["asset_duration"]) - duration))
        clip_id = f"beat-montage-{index + 1}-{candidate['asset_id']}"
        clips.append({"id": clip_id, "source_asset_id": str(candidate["asset_id"]), "source_start": round(source_start, 3), "source_end": round(source_start + duration, 3), "timeline_start": round(start, 3), "action": "keep", "confidence_score": round(min(100, 55 + float(candidate["score"]) * 45), 1), "reason": "AI selected the highest visual-motion window and aligned its cut to a BGM beat.", "kind": "beat_sync_montage"})
        if index:
            is_drop = any(abs(start - (drop - timeline_origin)) < .11 for drop in music.drops)
            kinds = ["white_flash", "black_flash", "camera_shake"] if is_drop else ["white_flash", "camera_shake"]
            kind = random.Random(f"{clip_id}:{start:.3f}").choice(kinds)
            effects.append({"time": round(start, 3), "kind": kind, "duration": .10 if "flash" in kind else .16, "target": "drop" if is_drop else "beat"})
    if not clips: raise BeatSyncError("No beat intervals were long enough to build a montage")
    return {"schema": "com.aivideo.beat-montage.v1", "source_asset_id": clips[0]["source_asset_id"], "tracks": [{"id": "main-video", "type": "main_video", "z_index": 0, "clips": clips}], "beat_sync_montage": {"tempo_bpm": music.tempo_bpm, "timeline_origin": timeline_origin, "beats": [round(item - timeline_origin, 3) for item in music.beats], "downbeats": [round(item - timeline_origin, 3) for item in music.downbeats if item >= timeline_origin], "drops": [round(item - timeline_origin, 3) for item in music.drops if item >= timeline_origin], "effects": effects, "aspect_ratio": aspect_ratio}}
