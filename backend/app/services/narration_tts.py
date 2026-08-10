"""Timeline-safe narration styles, final-WAV timing and export audio mixing."""
from __future__ import annotations

import re
import subprocess
import wave
from pathlib import Path
from typing import Any

from app.ai.providers.schemas import WordTimestamp
from app.schemas.subtitle import SubtitleCue


NARRATION_STYLES: dict[str, dict[str, str]] = {
    "energetic_girl": {"label": "元氣少女", "voice": "coral", "instructions": "Speak brightly, energetic and friendly, like a lively short-video host. Keep diction crisp."},
    "calm_narrator": {"label": "沉穩解說", "voice": "sage", "instructions": "Speak calmly, confident and clear, like a premium documentary narrator. Use measured pauses."},
    "funny_host": {"label": "搞怪幽默", "voice": "ballad", "instructions": "Speak playful and humorous with expressive, but intelligible, timing. Avoid impersonating any real person."},
    "warm_friend": {"label": "暖心朋友", "voice": "shimmer", "instructions": "Speak warmly and conversationally, like a supportive friend sharing a useful tip."},
    "cool_storyteller": {"label": "酷感故事家", "voice": "onyx", "instructions": "Speak with a cool, cinematic storytelling tone. Keep the delivery natural and engaging."},
}


def wav_duration_seconds(path: str | Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / max(1, source.getframerate())


def apply_pitch_shift(
    input_wav: str | Path,
    output_wav: str | Path,
    *,
    semitones: float,
    sample_rate: int | None = None,
) -> None:
    if abs(semitones) < .01:
        Path(output_wav).write_bytes(Path(input_wav).read_bytes()); return
    if sample_rate is None:
        with wave.open(str(input_wav), "rb") as source:
            sample_rate = source.getframerate()
    ratio = 2 ** (semitones / 12)
    command = ["ffmpeg", "-y", "-i", str(input_wav), "-af", f"asetrate={sample_rate * ratio:.3f},aresample={sample_rate}", str(output_wav)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Narration pitch processing timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError((exc.stderr or "Narration pitch processing failed")[-1500:]) from exc


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9']+|[,.!?，。！？；;]", text)


def narration_cues(text: str, *, start_time: float, duration: float, id_prefix: str) -> list[SubtitleCue]:
    """Create deterministic word timestamps that exactly occupy the final WAV duration."""
    tokens = _tokens(text); spoken = [token for token in tokens if not re.fullmatch(r"[,.!?，。！？；;]", token)]
    if not spoken:
        return []
    weights = [max(1, len(token)) for token in spoken]; total = sum(weights); cursor = start_time; cues: list[SubtitleCue] = []
    for index, (token, weight) in enumerate(zip(spoken, weights)):
        end = start_time + duration if index == len(spoken) - 1 else cursor + duration * weight / total
        word = WordTimestamp(word=token, start=round(cursor, 3), end=round(end, 3), confidence=1.0)
        cues.append(SubtitleCue(id=f"{id_prefix}-{index + 1:04d}", start_time=word.start, end_time=word.end, text=token, words=[word]))
        cursor = end
    return cues


def build_narration_mix_command(*, video_path: str, output_path: str, narrations: list[dict[str, Any]]) -> list[str]:
    command = ["ffmpeg", "-y", "-i", video_path]
    for narration in narrations:
        command.extend(["-i", str(narration["local_path"])])
    filters = ["[0:a]asetpts=PTS-STARTPTS[base]"]; labels = ["[base]"]
    for index, narration in enumerate(narrations, start=1):
        delay = max(0, round(float(narration["start_time"]) * 1000))
        label = f"tts{index}"; filters.append(f"[{index}:a]adelay={delay}:all=1,asetpts=PTS-STARTPTS[{label}]"); labels.append(f"[{label}]")
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[mix]")
    return command + ["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac", "-shortest", output_path]
