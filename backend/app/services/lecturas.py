"""Lecturas planning helpers and deterministic, reviewable Timeline dodge commands."""
from __future__ import annotations

import math
import subprocess
import wave
from pathlib import Path
from typing import Any


class LecturasError(RuntimeError):
    pass


def transcript_for_planning(settings: dict[str, Any]) -> list[dict[str, Any]]:
    items = dict(settings.get("subtitles", {})).get("items", [])
    transcript = [
        {"start_time": round(float(item.get("start_time", 0)), 3), "end_time": round(float(item.get("end_time", 0)), 3), "text": str(item.get("text", "")).strip()}
        for item in items if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
    if not transcript:
        raise LecturasError("Lecturas requires timestamped subtitles/transcript before planning")
    return transcript


def validate_plan(raw: dict[str, Any], *, max_interventions: int, output_duration: float) -> list[dict[str, Any]]:
    from app.schemas.lecturas import LecturasPlan

    try:
        plan = LecturasPlan.model_validate(raw)
    except Exception as exc:
        raise LecturasError("Multimodal provider returned an invalid Lecturas plan") from exc
    return sorted([
        item.model_dump(mode="json") for item in plan.interventions[:max_interventions]
        if item.anchor_output_time < max(0, output_duration - .15)
    ], key=lambda item: float(item["anchor_output_time"]))


def idle_assistant_motion(duration_seconds: float, *, fps: float = 30.0) -> dict[str, Any]:
    """Neutral idle rig: lip animation comes from Audio2Face; no source person's gesture is copied."""
    return {"format": "aivideo.avatar.rig.v1", "fps": fps, "frames": [
        {"time": round(index / fps, 4), "bones": {"spine": {"rotation_z": round(math.sin(index / fps * 1.1) * .7, 3)}, "head": {"yaw": round(math.sin(index / fps * .8) * 1.2, 3), "pitch": 0.0, "roll": 0.0}}}
        for index in range(max(1, math.ceil(duration_seconds * fps)))
    ]}


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as source:
            return source.getnframes() / max(1, source.getframerate())
    except wave.Error as exc:
        raise LecturasError("Lecturas TTS must produce a valid WAV file") from exc


def mux_avatar_with_voice(alpha_video: Path, narration_wav: Path, output: Path) -> None:
    try:
        result = subprocess.run(["ffmpeg", "-y", "-i", str(alpha_video), "-i", str(narration_wav), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(output)], capture_output=True, text=True, timeout=20 * 60)
    except subprocess.TimeoutExpired as exc:
        raise LecturasError("Assistant avatar A/V mux timed out") from exc
    if result.returncode:
        raise LecturasError(f"Assistant avatar A/V mux failed: {(result.stderr or '')[-1600:]}")


def build_pip_dodge_command(base_video: str, assistant_video: str, output: str, *, start: float, duration: float) -> list[str]:
    """Create a short two-host layout: lecturer shrinks left while assistant enters on the right."""
    graph = (
        "[0:v]split=2[background][lecturersource];"
        f"[background]drawbox=x=0:y=0:w=iw:h=ih:color=black@0.68:t=fill:enable='between(t\\,{start:.6f}\\,{start + duration:.6f})'[dimmed];"
        "[lecturersource]scale=w=iw*.56:h=ih*.56:force_original_aspect_ratio=decrease[lecturerv];"
        f"[dimmed][lecturerv]overlay=x=36:y=36:enable='between(t\\,{start:.6f}\\,{start + duration:.6f})'[hostpip];"
        f"[1:v]trim=duration={duration:.6f},setpts=PTS-STARTPTS+{start:.6f}/TB,scale=w=iw*.52:h=ih*.52:force_original_aspect_ratio=decrease[assistantv];"
        f"[hostpip][assistantv]overlay=x=W-w-36:y=H-h-36:eof_action=pass:enable='between(t\\,{start:.6f}\\,{start + duration:.6f})'[outv];"
        f"[1:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,adelay={int(round(start * 1000))}:all=1[assistanta];"
        "[0:a][assistanta]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=280[ducked];[ducked][assistanta]amix=inputs=2:duration=first:normalize=0[outa]"
    )
    return ["ffmpeg", "-y", "-i", base_video, "-i", assistant_video, "-filter_complex", graph, "-map", "[outv]", "-map", "[outa]", "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart", output]


def build_freeze_dodge_command(base_video: str, assistant_video: str, output: str, *, freeze_at: float, duration: float) -> list[str]:
    """Insert a frozen programme frame while the assistant slides in; Timeline length grows by narration duration."""
    margin = 36
    assistant_x = f"if(lt(t\\,0.35)\\,W-(w+{margin})*t/0.35\\,W-w-{margin})"
    graph = (
        f"[0:v]split=3[vprein][vstillin][vpostin];"
        f"[vprein]trim=start=0:end={freeze_at:.6f},setpts=PTS-STARTPTS[vpre];"
        f"[vstillin]trim=start={freeze_at:.6f}:end={freeze_at + .04:.6f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={duration:.6f}[freeze];"
        f"[vpostin]trim=start={freeze_at:.6f},setpts=PTS-STARTPTS[vpost];"
        f"[1:v]trim=duration={duration:.6f},setpts=PTS-STARTPTS,scale=w=iw*.52:h=ih*.52:force_original_aspect_ratio=decrease[assistantv];"
        f"[freeze][assistantv]overlay=x='{assistant_x}':y=H-h-36:eof_action=pass[insertv];"
        "[vpre][insertv][vpost]concat=n=3:v=1:a=0[outv];"
        "[0:a]asplit=2[aprein][apostin];"
        f"[aprein]atrim=start=0:end={freeze_at:.6f},asetpts=PTS-STARTPTS[apre];"
        f"[apostin]atrim=start={freeze_at:.6f},asetpts=PTS-STARTPTS[apost];"
        f"[1:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[assistanta];"
        "[apre][assistanta][apost]concat=n=3:v=0:a=1[outa]"
    )
    return ["ffmpeg", "-y", "-i", base_video, "-i", assistant_video, "-filter_complex", graph, "-map", "[outv]", "-map", "[outa]", "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-movflags", "+faststart", output]
