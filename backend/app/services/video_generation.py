"""Vendor-neutral B-Roll generation and self-hosted temporal outpainting adapters."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings


class VideoGenerationError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _raise_for_status(response: httpx.Response, provider: str) -> None:
    if response.is_error:
        detail = response.text[-1200:]
        raise VideoGenerationError(f"{provider} API returned {response.status_code}: {detail}", status_code=response.status_code)


class VideoGenerationProvider(ABC):
    name: str

    @abstractmethod
    def generate(
        self, *, prompt: str, duration_seconds: int, aspect_ratio: str, output_path: Path,
        reference_url: str | None = None, reference_path: Path | None = None,
    ) -> dict[str, Any]:
        """Block until an MP4 exists at ``output_path`` or raise a provider-aware error."""


class MockVideoGenerationProvider(VideoGenerationProvider):
    """Development-only clip generator; keeps UI/Timeline integration testable without vendor spend."""
    name = "mock_video_generation"

    def generate(self, *, prompt: str, duration_seconds: int, aspect_ratio: str, output_path: Path, reference_url: str | None = None, reference_path: Path | None = None) -> dict[str, Any]:
        del prompt, reference_url, reference_path
        width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)
        try:
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x172554:s={width}x{height}:r=30", "-t", str(max(3, min(5, duration_seconds))), "-vf", "drawgrid=w=96:h=96:t=1:c=0x38bdf8@0.22", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output_path)], check=True, capture_output=True, text=True, timeout=90)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise VideoGenerationError("Mock video generation failed") from exc
        return {"provider": self.name, "provider_task_id": "mock", "model": "ffmpeg-color-source", "requested_seconds": duration_seconds}


class RunwayVideoProvider(VideoGenerationProvider):
    name = "runway"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("RUNWAYML_API_SECRET")
        self.model = os.getenv("RUNWAY_VIDEO_MODEL", "gen4.5")
        self.version = os.getenv("RUNWAY_API_VERSION", "2024-11-06")

    def generate(self, *, prompt: str, duration_seconds: int, aspect_ratio: str, output_path: Path, reference_url: str | None = None, reference_path: Path | None = None) -> dict[str, Any]:
        del reference_path
        if not self.api_key:
            raise VideoGenerationError("RUNWAYML_API_SECRET is required for Runway video generation")
        ratio = "720:1280" if aspect_ratio == "9:16" else "1280:720"
        payload: dict[str, Any] = {"model": self.model, "promptText": prompt, "ratio": ratio, "duration": 5}
        if reference_url:
            payload["promptImage"] = reference_url
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Runway-Version": self.version, "Content-Type": "application/json"}
        with httpx.Client(timeout=httpx.Timeout(60, read=60)) as client:
            response = client.post("https://api.dev.runwayml.com/v1/image_to_video", headers=headers, json=payload)
            _raise_for_status(response, self.name)
            task_id = str(response.json()["id"])
            deadline = time.monotonic() + int(os.getenv("VIDEO_GENERATION_TIMEOUT_SECONDS", "1800"))
            while time.monotonic() < deadline:
                task = client.get(f"https://api.dev.runwayml.com/v1/tasks/{task_id}", headers=headers)
                _raise_for_status(task, self.name)
                data = task.json(); task_status = str(data.get("status", "")).upper()
                if task_status == "SUCCEEDED":
                    outputs = data.get("output") or []
                    if not outputs: raise VideoGenerationError("Runway task succeeded without an output URL")
                    result = client.get(str(outputs[0]), follow_redirects=True)
                    _raise_for_status(result, self.name); output_path.write_bytes(result.content)
                    return {"provider": self.name, "provider_task_id": task_id, "model": self.model, "requested_seconds": duration_seconds}
                if task_status in {"FAILED", "CANCELLED"}:
                    raise VideoGenerationError(f"Runway generation {task_status.lower()}: {data.get('failure') or data}")
                time.sleep(5)
        raise VideoGenerationError("Runway generation timed out")


class SoraVideoProvider(VideoGenerationProvider):
    name = "sora"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("SORA_VIDEO_MODEL", "sora-2")

    def generate(self, *, prompt: str, duration_seconds: int, aspect_ratio: str, output_path: Path, reference_url: str | None = None, reference_path: Path | None = None) -> dict[str, Any]:
        del reference_url
        if not self.api_key:
            raise VideoGenerationError("OPENAI_API_KEY is required for Sora video generation")
        # The current API accepts 4/8/12-second generations. We request 4 seconds then trim to
        # the editorial duration in the worker, preserving the product's 3--5 second contract.
        size = "720x1280" if aspect_ratio == "9:16" else "1280x720"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = {"model": self.model, "prompt": prompt, "seconds": "4", "size": size}
        files = {"input_reference": (reference_path.name, reference_path.read_bytes(), "image/jpeg")} if reference_path else None
        with httpx.Client(timeout=httpx.Timeout(90, read=90)) as client:
            response = client.post("https://api.openai.com/v1/videos", headers=headers, data=data, files=files)
            _raise_for_status(response, self.name)
            task_id = str(response.json()["id"])
            deadline = time.monotonic() + int(os.getenv("VIDEO_GENERATION_TIMEOUT_SECONDS", "1800"))
            while time.monotonic() < deadline:
                task = client.get(f"https://api.openai.com/v1/videos/{task_id}", headers=headers)
                _raise_for_status(task, self.name)
                details = task.json(); task_status = str(details.get("status", "")).lower()
                if task_status in {"completed", "succeeded"}:
                    result = client.get(f"https://api.openai.com/v1/videos/{task_id}/content", headers=headers, follow_redirects=True)
                    _raise_for_status(result, self.name); output_path.write_bytes(result.content)
                    return {"provider": self.name, "provider_task_id": task_id, "model": self.model, "requested_seconds": duration_seconds}
                if task_status in {"failed", "cancelled", "canceled"}:
                    raise VideoGenerationError(f"Sora generation {task_status}: {details.get('error') or details}")
                time.sleep(5)
        raise VideoGenerationError("Sora generation timed out")


def get_video_generation_provider(name: str | None = None) -> VideoGenerationProvider:
    if settings.use_mock_ai or (name or "").lower() == "mock": return MockVideoGenerationProvider()
    selected = (name or os.getenv("VIDEO_GENERATION_PROVIDER", "sora")).lower()
    if selected == "runway": return RunwayVideoProvider()
    if selected == "sora": return SoraVideoProvider()
    raise VideoGenerationError(f"Unsupported VIDEO_GENERATION_PROVIDER: {selected}")


def _terms(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    ignored = {"this", "that", "with", "from", "have", "your", "我們", "這個", "就是", "然後", "可以", "影片"}
    return [term for term, _ in Counter(term for term in words if term not in ignored).most_common(6)]


def build_broll_prompt(transcript: str, *, aspect_ratio: str) -> str:
    keywords = _terms(transcript)
    subject = ", ".join(keywords) or "the topic being explained"
    framing = "vertical 9:16 composition with clean center-safe space" if aspect_ratio == "9:16" else "cinematic 16:9 composition"
    return (
        f"Editorial B-roll illustrating {subject}. Natural documentary realism, {framing}, "
        "one coherent camera movement, no text, no logos, no watermarks, no talking head, "
        "consistent lighting and color, suitable as a silent overlay behind narration."
    )


def timeline_source_time(confirmed: dict[str, Any], output_time: float) -> float | None:
    segments = next((track.get("clips", []) for track in confirmed.get("tracks", []) if track.get("type") == "main_video"), confirmed.get("segments", []))
    cursor = 0.0
    for segment in segments:
        if segment.get("action", "keep") != "keep": continue
        duration = float(segment["source_end"]) - float(segment["source_start"])
        if cursor <= output_time <= cursor + duration: return float(segment["source_start"]) + output_time - cursor
        cursor += duration
    return None


def select_broll_opportunity(confirmed: dict[str, Any], subtitles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find a high-information speech window without an existing B-Roll overlay."""
    existing = [clip for track in confirmed.get("tracks", []) if track.get("type") == "b_roll" for clip in track.get("clips", [])]
    candidates: list[tuple[float, dict[str, Any]]] = []
    for cue in subtitles:
        start, end = float(cue.get("start_time", 0)), float(cue.get("end_time", 0))
        duration = max(.25, end - start); text = str(cue.get("text", "")); units = max(len(re.findall(r"[\u4e00-\u9fff]", text)), len(re.findall(r"\b\w+\b", text)))
        overlaps_broll = any(float(item.get("timeline_start", -999)) < end and float(item.get("timeline_start", 0)) + (float(item.get("source_end", 0)) - float(item.get("source_start", 0))) > start for item in existing)
        if not overlaps_broll and units / duration >= 3.2:
            candidates.append((units / duration, {"output_start": start, "duration_seconds": min(5.0, max(3.0, duration)), "transcript": text, "information_density": round(units / duration, 2)}))
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def visual_motion_score(video_path: Path, *, source_start: float, duration_seconds: float) -> float:
    """Mean frame difference in a short source window; lower values indicate a visually static shot."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VideoGenerationError("OpenCV and NumPy are required for B-Roll opportunity scoring") from exc
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened(): raise VideoGenerationError("Cannot decode video for B-Roll opportunity scoring")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, round(source_start * fps)))
    frames: list[Any] = []; last_index = round((source_start + duration_seconds) * fps); index = round(source_start * fps)
    try:
        while index <= last_index and len(frames) < 12:
            ok, frame = capture.read()
            if not ok: break
            if len(frames) == 0 or index % max(1, round(fps / 3)) == 0:
                frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            index += 1
    finally: capture.release()
    if len(frames) < 2: return 0.0
    return float(np.mean([np.mean(np.abs(current.astype(np.float32) - previous.astype(np.float32))) / 255 for previous, current in zip(frames, frames[1:])]))


class VideoOutpaintProvider(ABC):
    name: str

    @abstractmethod
    def outpaint(self, *, input_path: Path, output_path: Path, target_width: int, target_height: int, edge_manifest: Path) -> None:
        """Use temporal diffusion conditioned on source edges to create an expanded video."""


class StableVideoDiffusionCLIProvider(VideoOutpaintProvider):
    """Adapter for a GPU image/video-outpainting pipeline provisioned by the deployment team.

    A deployment may use SVD for temporal motion plus an inpainting/outpainting checkpoint for
    the expanded canvas. The exact inference repository stays external to avoid hard-coding a
    fork-specific CLI and model license into the web API.
    """
    name = "svd_cli"

    def outpaint(self, *, input_path: Path, output_path: Path, target_width: int, target_height: int, edge_manifest: Path) -> None:
        template = settings.svd_outpaint_command
        required = {"{input}", "{output}", "{width}", "{height}", "{edge_manifest}"}
        if not template or not required.issubset(set(re.findall(r"\{[^}]+\}", template))):
            raise VideoGenerationError("SVD_OUTPAINT_COMMAND must contain {input}, {output}, {width}, {height}, and {edge_manifest}")
        command = shlex.split(template.format(input=str(input_path), output=str(output_path), width=target_width, height=target_height, edge_manifest=str(edge_manifest)))
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=settings.video_generation_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise VideoGenerationError("Temporal outpainting timed out") from exc
        if result.returncode != 0:
            raise VideoGenerationError((result.stderr or result.stdout or "Temporal outpainting failed")[-2000:])
        if not output_path.exists():
            raise VideoGenerationError("Outpainting provider completed without creating output video")


def get_video_outpaint_provider() -> VideoOutpaintProvider:
    provider = settings.video_outpaint_provider.lower()
    if provider in {"svd", "svd_cli", "stable_video_diffusion"}: return StableVideoDiffusionCLIProvider()
    raise VideoGenerationError(f"Unsupported VIDEO_OUTPAINT_PROVIDER: {provider}")


def write_edge_manifest(video_path: Path, output_path: Path, *, target_width: int, target_height: int, sample_every_seconds: float = 1.0) -> None:
    """Persist edge-color evidence for the temporal diffusion runner; avoids a blurred-background fallback."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VideoGenerationError("OpenCV and NumPy are required to prepare outpainting context") from exc
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened(): raise VideoGenerationError("Cannot decode video for outpainting")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0; stride = max(1, round(fps * sample_every_seconds)); index = 0; samples: list[dict[str, Any]] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok: break
            if index % stride == 0:
                height, width = frame.shape[:2]; border = max(2, min(width, height) // 40)
                samples.append({"time": round(index / fps, 3), "source_size": [width, height], "target_size": [target_width, target_height], "top_bgr": np.mean(frame[:border, :, :], axis=(0, 1)).round(2).tolist(), "bottom_bgr": np.mean(frame[-border:, :, :], axis=(0, 1)).round(2).tolist()})
            index += 1
    finally: capture.release()
    output_path.write_text(json.dumps({"version": 1, "conditioning": "top_bottom_edge_pixels", "samples": samples}, ensure_ascii=False), encoding="utf-8")
