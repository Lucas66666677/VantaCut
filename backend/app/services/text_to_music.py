"""Timeline-duration music finishing and optional Spleeter accompaniment extraction."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class TextToMusicError(RuntimeError):
    pass


def timeline_duration_seconds(document: dict[str, object]) -> float:
    """Return visible programme length, retaining gaps and non-destructive offsets."""
    end_points: list[float] = []
    for track in document.get("tracks", []):
        if not isinstance(track, dict) or track.get("type") not in {"main_video", "multicam_video"}:
            continue
        for clip in track.get("clips", []):
            if not isinstance(clip, dict) or clip.get("action", "keep") != "keep":
                continue
            source_duration = max(0.0, float(clip.get("source_end", 0)) - float(clip.get("source_start", 0)))
            end_points.append(max(0.0, float(clip.get("timeline_start", 0))) + source_duration)
    return round(max(end_points, default=0.0), 3)


def _run(command: list[str], *, timeout_seconds: int) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise TextToMusicError("Music post-processing timed out") from exc
    except FileNotFoundError as exc:
        raise TextToMusicError(f"Required executable is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise TextToMusicError(f"Music post-processing failed: {(exc.stderr or '')[-1800:]}") from exc


def extract_accompaniment(source: Path, workdir: Path, *, command: str, timeout_seconds: int) -> Path:
    """Use the Spleeter 2-stem CLI only when vocals were detected and user requested instrumental."""
    output_dir = workdir / "spleeter"
    _run([command, "separate", "-p", "spleeter:2stems", "-o", str(output_dir), str(source)], timeout_seconds=timeout_seconds)
    accompaniment = output_dir / source.stem / "accompaniment.wav"
    if not accompaniment.exists():
        raise TextToMusicError("Spleeter did not produce an accompaniment stem")
    return accompaniment


def _probe_duration(source: Path, *, timeout_seconds: int) -> float:
    try:
        completed = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)], check=True, capture_output=True, text=True, timeout=timeout_seconds)
        return max(0.0, float(json.loads(completed.stdout).get("format", {}).get("duration", 0)))
    except Exception as exc:
        raise TextToMusicError("Unable to determine generated music duration") from exc


def finish_music(source: Path, output: Path, *, duration_seconds: float, timeout_seconds: int) -> dict[str, object]:
    """Beat-aware duration lock, with a deterministic FFmpeg loop fallback.

    A generator may return a shorter duration than requested.  The preferred path
    rebuilds intro/chorus/outro on detected bar boundaries; the fallback keeps a
    useful result available even when optional MIR dependencies are unavailable.
    """
    if duration_seconds < 1.0:
        raise TextToMusicError("Timeline must be at least one second for generated music")
    try:
        from app.services.smart_audio_remix import build_remix_command, estimate_music_sections, plan_smart_remix

        structure, sections, _ = estimate_music_sections(source)
        plan = plan_smart_remix(sections=sections, target_duration=duration_seconds, bpm=float(structure.tempo_bpm or 120))
        _run(build_remix_command(input_path=str(source), plan=plan, output_path=str(output)), timeout_seconds=timeout_seconds)
        return {"mode": "beat_aware_remix", "bpm": plan["bpm"], "crossfade_seconds": plan["crossfade_seconds"]}
    except Exception as beat_error:
        # `aloop` applies before `atrim`, so short provider outputs cannot leave
        # silent tail space. The final afade is still sample-accurately locked.
        fade_duration = min(2.0, max(.12, duration_seconds * .15))
        fade_start = max(0.0, duration_seconds - fade_duration)
        _run([
            "ffmpeg", "-y", "-i", str(source), "-vn",
            "-filter:a", f"aloop=loop=-1:size=2147483647,atrim=0:{duration_seconds:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}",
            "-t", f"{duration_seconds:.3f}", "-c:a", "aac", "-b:a", "192k", str(output),
        ], timeout_seconds=timeout_seconds)
        return {"mode": "loop_fallback", "beat_remix_error": str(beat_error)[-500:], "source_duration_seconds": _probe_duration(source, timeout_seconds=timeout_seconds)}
