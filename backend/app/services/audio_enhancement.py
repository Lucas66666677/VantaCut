import shlex
import subprocess
from pathlib import Path

from app.core.config import settings


NOISE_REDUCTION_FILTER = (
    "highpass=f=80,"
    "afftdn=nf=-25:nt=w,"
    "lowpass=f=15500,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)

# Fallback path for development or CPU-only workers. `anlmdn` removes broadband noise,
# while the high-pass stage reduces wind/engine rumble; speech normalisation and compression
# make close-mic narration fuller. A dedicated RNNoise/DeepFilterNet command is preferred.
STUDIO_SOUND_DSP_FILTER = (
    "highpass=f=110:width_type=h:width=90,"
    "afftdn=nf=-32:nt=w,"
    "anlmdn=s=0.000025:p=0.002:r=0.002:m=15,"
    "lowpass=f=16000,"
    "speechnorm=e=10:r=0.0001:l=1,"
    "acompressor=threshold=0.09:ratio=3.2:attack=12:release=180:makeup=1.8,"
    "loudnorm=I=-16:TP=-1.5:LRA=8"
)


def run_studio_sound_model(*, input_wav: Path, output_wav: Path) -> str:
    """Run a configured RNNoise/DeepFilterNet-compatible command, or a deterministic DSP fallback."""
    if settings.studio_sound_ai_command:
        command = shlex.split(settings.studio_sound_ai_command.format(input=str(input_wav), output=str(output_wav)), posix=False)
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=settings.studio_sound_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Studio Sound AI model timed out") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = exc.stderr[-2000:] if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
            raise RuntimeError(f"Studio Sound AI model failed: {detail}") from exc
        if not output_wav.exists() or output_wav.stat().st_size < 44:
            raise RuntimeError("Studio Sound AI model produced no WAV output")
        return "external_ai"

    try:
        subprocess.run(["ffmpeg", "-y", "-i", str(input_wav), "-af", STUDIO_SOUND_DSP_FILTER, "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(output_wav)], check=True, capture_output=True, text=True, timeout=settings.studio_sound_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Studio Sound DSP fallback timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Studio Sound DSP fallback failed: {(exc.stderr or '')[-2000:]}") from exc
    return "ffmpeg_dsp_fallback"


def build_studio_sound_mix_command(video_path: str, enhancements: list[dict[str, float | str]], output_path: str) -> list[str]:
    """Replace only enhanced clip windows with a dry/wet blend while preserving every other sound."""
    command = ["ffmpeg", "-y", "-i", video_path]
    for item in enhancements:
        command.extend(["-i", str(item["local_path"])])
    filters = ["[0:a]asetpts=PTS-STARTPTS[base]"]
    base_label = "base"
    wet_labels: list[str] = []
    for index, item in enumerate(enhancements, start=1):
        start, duration = float(item["timeline_start"]), float(item["duration"])
        wet = max(0.0, min(1.0, float(item["wet_mix"]) / 100.0)); dry = 1.0 - wet
        next_base, wet_label = f"dry{index}", f"wet{index}"
        filters.append(f"[{base_label}]volume=volume='if(between(t\\,{start:.3f}\\,{start + duration:.3f})\\,{dry:.5f}\\,1)':eval=frame[{next_base}]")
        filters.append(f"[{index}:a]atrim=duration={duration:.3f},adelay={round(start * 1000)}:all=1,volume={wet:.5f}[{wet_label}]")
        base_label = next_base; wet_labels.append(wet_label)
    filters.append(f"[{base_label}]{''.join(f'[{label}]' for label in wet_labels)}amix=inputs={len(wet_labels) + 1}:duration=first:normalize=0[studio]")
    return command + ["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[studio]", "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", output_path]


def has_noise_reduction(audio_effects: list[str] | None) -> bool:
    return "noise_reduction" in (audio_effects or [])
