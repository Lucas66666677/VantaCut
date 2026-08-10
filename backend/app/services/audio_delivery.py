"""EBU R128 loudness, creative stem mixing, and multi-track delivery helpers."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


AudioLoudnessTarget = Literal["broadcast", "streaming"]
AudioLayout = Literal["stereo", "5.1", "7.1.4"]


class AudioDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoudnessProfile:
    integrated_lufs: float
    true_peak_dbtp: float = -1.0
    loudness_range: float = 7.0


LOUDNESS_PROFILES: dict[AudioLoudnessTarget, LoudnessProfile] = {
    "broadcast": LoudnessProfile(integrated_lufs=-23.0, true_peak_dbtp=-1.0, loudness_range=7.0),
    "streaming": LoudnessProfile(integrated_lufs=-14.0, true_peak_dbtp=-1.0, loudness_range=11.0),
}


def _run(command: list[str], *, timeout_seconds: int = 60 * 60) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise AudioDeliveryError("Audio processing timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise AudioDeliveryError(f"FFmpeg audio processing failed: {(exc.stderr or '')[-3000:]}") from exc


def run_audio_command(command: list[str], *, timeout_seconds: int = 60 * 60) -> None:
    """Run a generated audio command with the delivery pipeline's error/timeout policy."""
    _run(command, timeout_seconds=timeout_seconds)


def _extract_loudnorm_json(stderr: str) -> dict[str, float]:
    for candidate in reversed(re.findall(r"\{\s*\"input_i\"[\s\S]*?\}", stderr)):
        try:
            parsed = json.loads(candidate)
            numeric: dict[str, float] = {}
            for key, value in parsed.items():
                try:
                    numeric[key] = float(value)
                except (TypeError, ValueError):
                    continue
            return numeric
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    raise AudioDeliveryError("FFmpeg loudnorm measurement JSON was not found")


def layout_filter(layout: AudioLayout) -> str | None:
    if layout == "stereo":
        return None
    # Conservative stereo-to-5.1 upmix: phantom centre/rear ambience, silent LFE.
    if layout == "5.1":
        return "pan=5.1|FL=0.707*FL|FR=0.707*FR|FC=0.5*FL+0.5*FR|LFE=0.0*FL|BL=0.5*FL|BR=0.5*FR"
    return "pan=7.1.4|FL=0.65*FL|FR=0.65*FR|FC=0.5*FL+0.5*FR|LFE=0.0*FL|BL=0.35*FL|BR=0.35*FR|SL=0.45*FL|SR=0.45*FR|TFL=0.22*FL|TFR=0.22*FR|TBL=0.16*FL|TBR=0.16*FR"


def measure_loudness(input_path: str, target: AudioLoudnessTarget, *, layout: AudioLayout = "stereo") -> dict[str, float]:
    profile = LOUDNESS_PROFILES[target]
    filters = [item for item in (layout_filter(layout),) if item]
    filters.append(
        f"loudnorm=I={profile.integrated_lufs}:TP={profile.true_peak_dbtp}:LRA={profile.loudness_range}:print_format=json"
    )
    completed = _run(["ffmpeg", "-hide_banner", "-i", input_path, "-vn", "-af", ",".join(filters), "-f", "null", "-"])
    return _extract_loudnorm_json(completed.stderr)


def loudnorm_second_pass_filter(measurement: dict[str, float], target: AudioLoudnessTarget) -> str:
    profile = LOUDNESS_PROFILES[target]
    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    missing = [key for key in required if key not in measurement]
    if missing:
        raise AudioDeliveryError(f"Incomplete loudnorm measurement: {', '.join(missing)}")
    return (
        f"loudnorm=I={profile.integrated_lufs}:TP={profile.true_peak_dbtp}:LRA={profile.loudness_range}:"
        f"measured_I={measurement['input_i']}:measured_TP={measurement['input_tp']}:"
        f"measured_LRA={measurement['input_lra']}:measured_thresh={measurement['input_thresh']}:"
        f"offset={measurement['target_offset']}:linear=true:print_format=summary"
    )


def normalise_media_audio(
    input_path: str,
    output_path: str,
    *,
    target: AudioLoudnessTarget,
    layout: AudioLayout = "stereo",
    container: Literal["mp4", "mov"] = "mp4",
    already_spatial: bool = False,
) -> dict[str, float]:
    """Two-pass `loudnorm`: measure complete edited mix, then render against measured values."""
    if layout == "7.1.4" and container != "mov":
        raise AudioDeliveryError("7.1.4 channel-bed delivery requires MOV with PCM audio; use Dolby tooling for Atmos encoding")
    if layout == "7.1.4":
        # FFmpeg loudnorm does not reliably produce integrated-LUFS measurements for 12-channel
        # beds. Preserve the authored spatial balance and apply a transparent peak safety limit;
        # broadcast Atmos masters should be measured in a Dolby/immersive-aware renderer.
        command = [
            "ffmpeg", "-y", "-i", input_path, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
            "-af", "alimiter=limit=0.891251", "-c:a", "pcm_s24le", "-ac", "12", output_path,
        ]
        _run(command)
        return {"peak_limit_dbtp": -1.0}
    measurement = measure_loudness(input_path, target, layout=layout if not already_spatial else "stereo")
    filters = [item for item in (() if already_spatial else (layout_filter(layout),)) if item]
    filters.append(loudnorm_second_pass_filter(measurement, target))
    audio_codec = "pcm_s24le" if layout == "7.1.4" else "aac"
    command = [
        "ffmpeg", "-y", "-i", input_path, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
        "-af", ",".join(filters), "-c:a", audio_codec,
        "-ac", "12" if layout == "7.1.4" else "6" if layout == "5.1" else "2",
    ]
    if audio_codec == "aac":
        command.extend(["-b:a", "384k" if layout == "5.1" else "192k"])
    if container == "mp4":
        command.extend(["-movflags", "+faststart"])
    command.append(output_path)
    _run(command)
    return measurement


def mix_spatial_soundscape(
    video_path: str,
    soundscape_path: str,
    output_path: str,
    *,
    layout: Literal["5.1", "7.1.4"],
) -> None:
    """Upmix dialogue safely, then add a layout-matched spatial ambience/foley bed."""
    dialogue_filter = layout_filter(layout)
    if dialogue_filter is None:
        raise AudioDeliveryError("Spatial soundscape requires a multichannel layout")
    command = ["ffmpeg", "-y", "-i", video_path, "-i", soundscape_path, "-filter_complex", (
        f"[0:a]{dialogue_filter}[dialogue];"
        f"[1:a]aformat=channel_layouts={layout}[soundscape];"
        "[dialogue][soundscape]amix=inputs=2:normalize=0:duration=first[mix]"
    ), "-map", "0:v:0", "-map", "[mix]", "-c:v", "copy", "-c:a", "pcm_s24le", output_path]
    _run(command)


def run_dolby_atmos_encoder(video_path: str, adm_bwf_path: str, output_path: str) -> None:
    """Delegate final JOC/TrueHD Atmos encoding to a licensed Dolby-capable tool.

    FFmpeg remains responsible for the picture and channel-bed preview.  It does not author
    Dolby object metadata, so this explicit handoff prevents a plain multichannel file from
    being incorrectly labelled as Atmos.
    """
    template = os.getenv("DOLBY_ATMOS_ENCODER_COMMAND", "")
    required = {"{video}", "{adm_bwf}", "{output}"}
    if not template or not required.issubset(set(re.findall(r"\{[^}]+\}", template))):
        raise AudioDeliveryError("DOLBY_ATMOS_ENCODER_COMMAND must contain {video}, {adm_bwf}, and {output}")
    command = __import__("shlex").split(template.format(video=video_path, adm_bwf=adm_bwf_path, output=output_path))
    _run(command, timeout_seconds=4 * 60 * 60)
    if not Path(output_path).exists():
        raise AudioDeliveryError("Dolby encoder did not create an Atmos deliverable")


def _timeline_audio_filter(input_index: int, segments: list[dict[str, Any]], output_label: str) -> str:
    pieces: list[str] = []
    for index, segment in enumerate(segments):
        if segment.get("action", "keep") != "keep":
            continue
        start, end = float(segment["source_start"]), float(segment["source_end"])
        if end <= start:
            raise AudioDeliveryError("Invalid Timeline segment for stem mix")
        label = f"stem{input_index}_{index}"
        pieces.append(f"[{input_index}:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[{label}]")
    labels = re.findall(r"\[(stem\d+_\d+)\]", ";".join(pieces))
    if not labels:
        raise AudioDeliveryError("Timeline has no kept segments for stem delivery")
    return ";".join([*pieces, f"{''.join(f'[{label}]' for label in labels)}concat=n={len(labels)}:v=0:a=1[{output_label}]"])


def _stem_effect_filter(label: str, output_label: str, settings: dict[str, Any]) -> str:
    filters = [f"[{label}]"]
    for band in settings.get("eq", []):
        filters.append(
            "equalizer="
            f"f={float(band['frequency_hz'])}:width_type=o:width={float(band['width_octaves'])}:g={float(band['gain_db'])},"
        )
    gain_db = -120.0 if settings.get("mute", False) else float(settings.get("gain_db", 0))
    filters.append(f"volume={gain_db}dB[{output_label}]")
    return "".join(filters)


def build_stem_mix_command(
    video_path: str,
    stem_paths: dict[str, str],
    segments: list[dict[str, Any]],
    stem_settings: dict[str, dict[str, Any]],
    output_path: str,
) -> list[str]:
    """Create an edited, non-destructive Dialogue/Music/SFX mix aligned to kept Timeline ranges."""
    required = ("dialogue", "music", "sfx")
    if any(name not in stem_paths for name in required):
        raise AudioDeliveryError("Dialogue, music, and SFX stem paths are all required")
    command = ["ffmpeg", "-y", "-i", video_path]
    for name in required:
        command.extend(["-i", stem_paths[name]])
    filters: list[str] = []
    processed: list[str] = []
    for input_index, name in enumerate(required, start=1):
        timeline_label, processed_label = f"{name}_timeline", f"{name}_processed"
        filters.append(_timeline_audio_filter(input_index, segments, timeline_label))
        filters.append(_stem_effect_filter(timeline_label, processed_label, stem_settings.get(name, {})))
        processed.append(processed_label)
    filters.append(f"{''.join(f'[{label}]' for label in processed)}amix=inputs=3:normalize=0:dropout_transition=0[finalmix]")
    return command + [
        "-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[finalmix]",
        "-c:v", "copy", "-c:a", "pcm_s16le", output_path,
    ]


def render_timeline_stem_files(
    stem_paths: dict[str, str],
    segments: list[dict[str, Any]],
    output_directory: Path,
    stem_settings: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Produce Timeline-trimmed files so auxiliary MP4/MOV tracks match the final cut duration."""
    output_directory.mkdir(parents=True, exist_ok=True)
    rendered: dict[str, str] = {}
    for name, input_path in stem_paths.items():
        output = output_directory / f"{name}-timeline.wav"
        filters = [
            _timeline_audio_filter(0, segments, "timelinea"),
            _stem_effect_filter("timelinea", "outa", stem_settings.get(name, {})),
        ]
        command = ["ffmpeg", "-y", "-i", input_path, "-filter_complex", ";".join(filters), "-map", "[outa]", "-c:a", "pcm_s16le", str(output)]
        _run(command)
        rendered[name] = str(output)
    return rendered


def mux_multitrack_delivery(
    final_mix_path: str,
    stem_paths: dict[str, str],
    output_path: str,
    *,
    container: Literal["mp4", "mov"],
) -> None:
    """Package final mix plus editable Dialogue/Music/SFX tracks in MP4 or MOV."""
    command = ["ffmpeg", "-y", "-i", final_mix_path]
    probe = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", final_mix_path])
    existing_audio_tracks = len([line for line in probe.stdout.splitlines() if line.strip()])
    ordered_names = [name for name in ("dialogue", "music", "sfx") if name in stem_paths]
    for name in ordered_names:
        command.extend(["-i", stem_paths[name]])
    # Preserve existing alternate tracks, including the selectable Audio
    # Description track, before adding editable Dialogue/Music/SFX stems.
    command.extend(["-map", "0:v:0", "-map", "0:a?"])
    for index in range(1, len(ordered_names) + 1):
        command.extend(["-map", f"{index}:a:0"])
    command.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", "256k"])
    command.extend(["-metadata:s:a:0", "title=Final Mix"])
    for index, name in enumerate(ordered_names, start=existing_audio_tracks):
        command.extend([f"-metadata:s:a:{index}", f"title={name.upper()}"])
    if container == "mp4":
        command.extend(["-movflags", "+faststart"])
    command.append(output_path)
    _run(command)
