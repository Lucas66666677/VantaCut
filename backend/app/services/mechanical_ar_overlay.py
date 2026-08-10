"""RGBA teaching overlay for tracked parts, motion vectors, inferred signal flow, and source highlights."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


class MechanicalAROverlayError(RuntimeError):
    pass


def _font(size: int) -> ImageFont.ImageFont:
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _active(effects: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    return [item for item in effects if float(item.get("output_start", 0)) <= now <= float(item.get("output_end", 0))]


def _bbox(item: dict[str, Any], width: int, height: int) -> tuple[float, float, float, float]:
    box = dict(item.get("bbox_norm", {})); x, y = float(box.get("x", 0)) * width, float(box.get("y", 0)) * height
    return x, y, x + float(box.get("width", 0)) * width, y + float(box.get("height", 0)) * height


def _arrow(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float], color: tuple[int, int, int, int], width: int = 5) -> None:
    draw.line([start, end], fill=color, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0]); size = 13
    for delta in (2.55, -2.55):
        draw.line([end, (end[0] + size * math.cos(angle + delta), end[1] + size * math.sin(angle + delta))], fill=color, width=width)


def render_mechanical_ar_webm(effects: list[dict[str, Any]], output_path: str | Path, *, duration: float, width: int, height: int, fps: int = 30) -> Path:
    if duration <= 0:
        raise MechanicalAROverlayError("Mechanical AR overlay duration must be positive")
    output = Path(output_path); command = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "-", "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", "30", str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for index in range(math.ceil((duration + .1) * fps)):
            now = index / fps; image = Image.new("RGBA", (width, height), (0, 0, 0, 0)); draw = ImageDraw.Draw(image)
            for effect in _active(effects, now):
                kind = str(effect["type"])
                if kind == "code_highlight":
                    font = _font(max(15, width // 60)); x0, y0, card_width = int(width * .63), int(height * .10), int(width * .34)
                    draw.rounded_rectangle((x0, y0, x0 + card_width, y0 + int(height * .23)), radius=18, fill=(11, 19, 34, 235), outline=(72, 214, 255, 245), width=3)
                    draw.text((x0 + 18, y0 + 16), f"{effect.get('action', 'action')}  ·  line {effect.get('line_start')}", font=font, fill=(104, 224, 255, 255))
                    draw.text((x0 + 18, y0 + 52), str(effect.get("snippet", "")), font=font, fill=(230, 245, 255, 255), spacing=4)
                    continue
                x1, y1, x2, y2 = _bbox(effect, width, height); center = ((x1 + x2) / 2, (y1 + y2) / 2)
                if kind == "gear_rotation":
                    color = (251, 191, 36, 245); radius = max(22, min(x2 - x1, y2 - y1) * .62); draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), outline=color, width=4)
                    direction = 1 if float(effect.get("angular_velocity", 0)) >= 0 else -1; angle = now * 7 * direction; end = (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)); _arrow(draw, center, end, color)
                elif kind == "motion_vector":
                    color = (110, 231, 183, 245); draw.rounded_rectangle((x1, y1, x2, y2), radius=10, outline=color, width=3); _arrow(draw, center, (center[0] + float(effect.get("dx", 0)) * 15, center[1] + float(effect.get("dy", 0)) * 15), color)
                elif kind == "illustrative_signal_flow":
                    color = (96, 165, 250, 235); draw.rounded_rectangle((x1, y1, x2, y2), radius=8, outline=color, width=3)
                    phase = (now * 70) % max(1, x2 - x1); _arrow(draw, (x1 + phase, (y1 + y2) / 2), (min(x2, x1 + phase + 30), (y1 + y2) / 2), color, 4)
            process.stdin.write(np.asarray(image, dtype=np.uint8).tobytes())
        process.stdin.close(); stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        if process.wait(timeout=20 * 60) != 0:
            raise MechanicalAROverlayError(stderr[-2000:] or "Mechanical AR alpha render failed")
    except (BrokenPipeError, subprocess.TimeoutExpired) as exc:
        process.kill(); raise MechanicalAROverlayError("Mechanical AR alpha render failed") from exc
    return output


def build_mechanical_ar_overlay_command(base_video: str, overlay_webm: str, output_video: str) -> list[str]:
    return ["ffmpeg", "-y", "-i", base_video, "-i", overlay_webm, "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[outv]", "-map", "[outv]", "-map", "0:a?", "-c:v", "libx264", "-preset", "fast", "-c:a", "copy", "-movflags", "+faststart", output_video]
