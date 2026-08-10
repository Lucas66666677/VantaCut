"""Headless Unreal MRQ renderer adapter for transparent avatar pass output."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.config import settings


class AvatarRenderError(RuntimeError):
    pass


def render_avatar_rgba(*, animation_path: Path, avatar_bundle_path: Path, output_path: Path, width: int, height: int) -> Path:
    """Invoke a project-provided UE Python MRQ executor.

    The executor reads -AvatarAnimation/-AvatarBundle and must write a ProRes 4444 MOV or alpha WebM to -AvatarOutput.
    UE project settings must enable Movie Render Queue and Alpha Output; this worker never fabricates a non-alpha fallback.
    """
    if not settings.avatar_unreal_command or not settings.avatar_unreal_project:
        raise AvatarRenderError("Avatar rendering requires AVATAR_UNREAL_COMMAND and AVATAR_UNREAL_PROJECT")
    command = [
        settings.avatar_unreal_command, settings.avatar_unreal_project, "-game", "-windowed", "-unattended", "-NoSound",
        f"-AvatarAnimation={animation_path}", f"-AvatarBundle={avatar_bundle_path}", f"-AvatarOutput={output_path}",
        f"-ResX={width}", f"-ResY={height}",
        "-MoviePipelineLocalExecutorClass=/Script/MovieRenderPipelineCore.MoviePipelinePythonHostExecutor",
        "-ExecutorPythonClass=/Engine/PythonTypes.AvatarMRQExecutor",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=settings.avatar_render_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise AvatarRenderError("Unreal avatar render timed out") from exc
    if completed.returncode != 0 or not output_path.exists():
        raise AvatarRenderError((completed.stderr or completed.stdout or "Unreal avatar render failed")[-3000:])
    return output_path
