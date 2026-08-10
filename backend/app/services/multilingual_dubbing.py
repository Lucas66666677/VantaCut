"""Translation, duration alignment, and external lip-sync orchestration for picture-locked renders."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai.providers.base import TextAnalysisProvider


class DubbingError(RuntimeError): pass


TRANSLATION_SYSTEM_PROMPT = """You translate timed Chinese video dialogue for spoken dubbing. Return JSON only: {\"translations\":[{\"cue_id\":string,\"text\":string}]}. Preserve cue IDs exactly. Write natural, concise spoken target-language wording; do not add facts, stage directions, or markdown. Prefer phrases that fit the original speaking slot."""


@dataclass(frozen=True)
class DubCue:
    cue_id: str
    source_start: float
    source_end: float
    text: str


@dataclass(frozen=True)
class CueTiming:
    cue_id: str
    output_start: float
    speech_duration: float
    atempo: float
    next_pause_speed: float | None


def translate_cues(provider: TextAnalysisProvider, cues: list[DubCue], target_language: str) -> list[DubCue]:
    response = provider.generate_structured_json(system_prompt=TRANSLATION_SYSTEM_PROMPT, user_prompt=json.dumps({"target_language": target_language, "cues": [{"cue_id": item.cue_id, "text": item.text} for item in cues]}, ensure_ascii=False), response_schema={"type": "object"})
    translated = {str(item.get("cue_id")): str(item.get("text", "")).strip() for item in response.get("translations", []) if item.get("text")}
    # Mock/failing structured providers still yield an auditable development placeholder, not invented content.
    return [DubCue(item.cue_id, item.source_start, item.source_end, translated.get(item.cue_id, item.text),) for item in cues]


def build_timing_plan(cues: list[DubCue], raw_durations: dict[str, float], *, min_rate: float = .85, max_rate: float = 1.18) -> list[CueTiming]:
    """Keep speech speed natural, then absorb residual duration only inside following silent gaps."""
    output_cursor = cues[0].source_start if cues else 0.0; result: list[CueTiming] = []
    for index, cue in enumerate(cues):
        slot = cue.source_end - cue.source_start; raw = raw_durations[cue.cue_id]
        atempo = min(max_rate, max(min_rate, raw / max(slot, .01)))
        spoken = raw / atempo
        next_pause = (cues[index + 1].source_start - cue.source_end) if index + 1 < len(cues) else 0.0
        residual = spoken - slot
        target_pause = max(.08, next_pause - residual) if next_pause else 0.0
        pause_speed = (next_pause / target_pause) if next_pause and target_pause else None
        result.append(CueTiming(cue.cue_id, output_cursor, spoken, atempo, pause_speed))
        output_cursor += slot + target_pause
    return result


def atempo_filter(rate: float) -> str:
    if not .5 <= rate <= 2: raise DubbingError("atempo rate must be in [0.5, 2]")
    return f"atempo={rate:.8f}"


def build_pause_stretch_command(input_video: str, cues: list[DubCue], timing: list[CueTiming], output_video: str, *, video_duration: float) -> list[str]:
    """Apply `setpts` only to non-speaking gaps; spoken shots retain their original frame rate."""
    if not cues: raise DubbingError("No subtitle cues for time-stretch plan")
    parts: list[str] = []; labels: list[str] = []; part = 0
    if cues[0].source_start > .01:
        parts.append(f"[0:v]trim=start=0:end={cues[0].source_start:.6f},setpts=PTS-STARTPTS[v{part}]"); labels.append(f"[v{part}]"); part += 1
    for index, cue in enumerate(cues):
        label = f"v{part}"; parts.append(f"[0:v]trim=start={cue.source_start:.6f}:end={cue.source_end:.6f},setpts=PTS-STARTPTS[{label}]"); labels.append(f"[{label}]"); part += 1
        if index + 1 < len(cues):
            pause_start, pause_end = cue.source_end, cues[index + 1].source_start
            if pause_end > pause_start + .01:
                speed = timing[index].next_pause_speed or 1.0; label = f"v{part}"
                parts.append(f"[0:v]trim=start={pause_start:.6f}:end={pause_end:.6f},setpts=PTS/{speed:.8f}[{label}]"); labels.append(f"[{label}]"); part += 1
    if video_duration > cues[-1].source_end + .01:
        parts.append(f"[0:v]trim=start={cues[-1].source_end:.6f}:end={video_duration:.6f},setpts=PTS-STARTPTS[v{part}]"); labels.append(f"[v{part}]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[outv]")
    return ["ffmpeg", "-y", "-i", input_video, "-filter_complex", ";".join(parts), "-map", "[outv]", "-an", "-c:v", "libx264", "-preset", "fast", output_video]


def build_dub_audio_command(cue_paths: list[tuple[CueTiming, str]], output_audio: str, *, duration_seconds: float) -> list[str]:
    command = ["ffmpeg", "-y"]; filters: list[str] = []
    for _, path in cue_paths: command.extend(["-i", path])
    for index, (timing, _) in enumerate(cue_paths): filters.append(f"[{index}:a]{atempo_filter(timing.atempo)},adelay={int(timing.output_start*1000)}:all=1[a{index}]")
    filters.append(f"{' '.join(f'[a{i}]' for i in range(len(cue_paths)))}amix=inputs={len(cue_paths)}:normalize=0,apad,atrim=duration={duration_seconds:.6f}[outa]")
    return command + ["-filter_complex", ";".join(filters), "-map", "[outa]", "-c:a", "pcm_s16le", output_audio]


def build_background_preserving_mix_command(background_audio: str, dubbed_audio: str, output_audio: str, *, duration_seconds: float) -> list[str]:
    """Mix a separately prepared music/SFX bed with dubbed dialogue; never mix the original dialogue track back in."""
    return ["ffmpeg", "-y", "-i", background_audio, "-i", dubbed_audio, "-filter_complex", f"[0:a]atrim=duration={duration_seconds:.6f}[bed];[1:a]atrim=duration={duration_seconds:.6f}[dub];[bed][dub]amix=inputs=2:normalize=0[outa]", "-map", "[outa]", "-c:a", "pcm_s16le", output_audio]


def run_lip_sync(provider: str, picture_lock: str, audio: str, output: str) -> dict[str, Any]:
    from app.core.config import settings
    if settings.use_mock_ai:
        subprocess.run(["ffmpeg", "-y", "-i", picture_lock, "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", output], check=True, capture_output=True, text=True, timeout=60*60)
        return {"provider": "mock_passthrough", "requires_visual_review": True}
    command_template = settings.wav2lip_command if provider == "wav2lip" else settings.sadtalker_command
    if provider == "wav2lip" and not settings.wav2lip_commercial_licensed:
        raise DubbingError("Wav2Lip open-source weights are not licensed for commercial use; configure a licensed provider")
    if not command_template: raise DubbingError(f"{provider} command is not configured on the GPU worker")
    command = command_template.format(video=picture_lock, audio=audio, output=output)
    result = subprocess.run(command, shell=True, check=False, capture_output=True, text=True, timeout=4*60*60)
    if result.returncode or not Path(output).is_file(): raise DubbingError(f"Lip-sync inference failed: {result.stderr[-1500:]}")
    return {"provider": provider, "requires_visual_review": True}
