"""RGBA WebM fallback for kinetic captions that exceed ASS/libass capabilities."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.schemas.subtitle import SubtitleCue


class KineticOverlayError(RuntimeError):
    pass


def _font(size: int, font_path: str | None = None) -> ImageFont.ImageFont:
    candidates = [font_path] if font_path else []
    candidates.extend([
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ])
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _ease_out_back(progress: float) -> float:
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (progress - 1) ** 3 + c1 * (progress - 1) ** 2


def _draw_word(draw: ImageDraw.ImageDraw, word, x: float, y: float, now: float, font: ImageFont.ImageFont) -> None:
    duration = max(.08, word.end - word.start)
    progress = max(0.0, min(1.0, (now - word.start) / duration))
    preset = word.animation_preset
    if preset == "explode":
        for particle_index in range(18):
            angle = particle_index * math.tau / 18 + sum(map(ord, word.word)) * .01
            distance = 20 + 190 * progress * progress
            px, py = x + math.cos(angle) * distance, y + math.sin(angle) * distance
            alpha = int(255 * max(0.0, 1 - progress))
            radius = max(1, int(5 * (1 - progress)))
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=(255, 228, 80, alpha))
        if progress > .12:
            return
    scale = 1.0
    offset_y = 0.0
    fill = (255, 255, 255, 255)
    if preset == "spring":
        scale = .7 + .3 * _ease_out_back(min(1.0, progress * 4))
    elif preset == "pop":
        offset_y = (1 - min(1.0, progress * 5)) * 70
        scale = 1.3 - .3 * min(1.0, progress * 5)
    elif preset == "shake":
        x += math.sin(progress * 46) * 11 * (1 - progress)
        fill = (255, 110, 100, 255)
    elif preset == "float":
        offset_y = (1 - min(1.0, progress * 3)) * 20
        fill = (196, 224, 255, 235)
    # Pillow cannot scale an already positioned glyph cheaply; use a font-size approximation per frame.
    if scale != 1:
        sized_font = _font(max(12, int(getattr(font, "size", 64) * scale)))
    else:
        sized_font = font
    draw.text((x, y - offset_y), word.word, font=sized_font, anchor="mm", fill=fill, stroke_width=3, stroke_fill=(12, 12, 18, 235))


def render_kinetic_webm(
    cues: list[SubtitleCue],
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    font_path: str | None = None,
) -> None:
    """Render alpha-preserving VP9/WebM captions; use this for particle-rich export presets."""
    if not cues:
        raise KineticOverlayError("No subtitle cues to render")
    output = Path(output_path)
    duration = max(cue.end_time for cue in cues) + .1
    font = _font(max(26, round(height * .06)), font_path)
    command = [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{width}x{height}",
        "-framerate", str(fps), "-i", "-", "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-auto-alt-ref", "0", "-b:v", "0", "-crf", "30", str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for frame_index in range(math.ceil(duration * fps)):
            now = frame_index / fps
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            active = [cue for cue in cues if cue.start_time <= now <= cue.end_time]
            for cue in active:
                words = cue.words or []
                if not words:
                    continue
                # Approximate word anchors; precise browser typography remains the preview authority.
                total = sum(draw.textlength(word.word, font=font) + 18 for word in words)
                cursor = (width - total) / 2
                for word in words:
                    advance = draw.textlength(word.word, font=font) + 18
                    if word.start <= now <= word.end:
                        _draw_word(draw, word, cursor + advance / 2, height * .86, now, font)
                    cursor += advance
            process.stdin.write(np.asarray(image, dtype=np.uint8).tobytes())
        process.stdin.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        if process.wait(timeout=15 * 60) != 0:
            raise KineticOverlayError(stderr[-2000:] or "FFmpeg alpha WebM rendering failed")
    except (BrokenPipeError, subprocess.TimeoutExpired) as exc:
        process.kill()
        raise KineticOverlayError("Kinetic subtitle alpha render failed") from exc


def build_kinetic_overlay_command(base_video: str, kinetic_webm: str, output_video: str) -> list[str]:
    """Composite an alpha WebM after the primary edit; preserve the original audio stream."""
    return [
        "ffmpeg", "-y", "-i", base_video, "-i", kinetic_webm,
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[outv]",
        "-map", "[outv]", "-map", "0:a?", "-c:v", "libx264", "-preset", "fast", "-c:a", "copy",
        "-movflags", "+faststart", output_video,
    ]
