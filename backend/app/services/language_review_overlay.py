"""Render timestamped red/green correction and synonym-card overlays with alpha."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


class LanguageReviewOverlayError(RuntimeError):
    pass


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in ("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _active(items: list[dict[str, Any]], now: float, kind: str) -> dict[str, Any] | None:
    return next((item for item in items if item.get("type") == kind and float(item.get("output_start", 0)) <= now <= float(item.get("output_end", 0))), None)


def _correction(draw: ImageDraw.ImageDraw, item: dict[str, Any], width: int, height: int) -> None:
    font, small = _font(max(28, width // 34)), _font(max(18, width // 58))
    x, y, card_w, card_h = width // 2, int(height * .12), int(width * .78), int(height * .18)
    draw.rounded_rectangle((x - card_w // 2, y, x + card_w // 2, y + card_h), radius=26, fill=(20, 20, 28, 220), outline=(255, 110, 110, 240), width=3)
    original, correction = str(item.get("original_text", "")), str(item.get("correction", ""))
    draw.text((x, y + card_h * .35), original, anchor="mm", font=font, fill=(255, 100, 100, 255))
    original_width = draw.textlength(original, font=font)
    draw.line((x - original_width / 2, y + card_h * .35, x + original_width / 2, y + card_h * .35), fill=(255, 72, 72, 255), width=4)
    draw.text((x, y + card_h * .68), f"✓ {correction}", anchor="mm", font=font, fill=(110, 245, 160, 255))
    draw.text((x, y + card_h - 18), str(item.get("explanation", "")), anchor="ms", font=small, fill=(225, 225, 235, 245))


def _synonym_card(draw: ImageDraw.ImageDraw, item: dict[str, Any], width: int, height: int) -> None:
    font, small = _font(max(23, width // 48)), _font(max(16, width // 68))
    card_w, card_h, x, y = int(width * .34), int(height * .22), int(width * .79), int(height * .68)
    draw.rounded_rectangle((x - card_w // 2, y, x + card_w // 2, y + card_h), radius=22, fill=(34, 25, 58, 225), outline=(180, 135, 255, 245), width=3)
    draw.text((x, y + 32), f"Upgrade: {item.get('term', '')}", anchor="mm", font=font, fill=(218, 190, 255, 255))
    for index, synonym in enumerate(list(item.get("synonyms", []))[:3]):
        term, reason = str(synonym.get("term", "")), str(synonym.get("reason", ""))
        draw.text((x - card_w * .42, y + 76 + index * 38), term, anchor="lm", font=font, fill=(135, 255, 188, 255))
        draw.text((x - card_w * .42, y + 100 + index * 38), reason, anchor="lm", font=small, fill=(236, 232, 248, 245))


def render_language_review_webm(items: list[dict[str, Any]], output_path: str | Path, *, duration: float, width: int, height: int, fps: int = 30) -> Path:
    if duration <= 0:
        raise LanguageReviewOverlayError("Overlay duration must be positive")
    output = Path(output_path); command = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "-", "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", "30", str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for frame in range(math.ceil((duration + .1) * fps)):
            now = frame / fps; image = Image.new("RGBA", (width, height), (0, 0, 0, 0)); draw = ImageDraw.Draw(image)
            correction, synonym = _active(items, now, "grammar_correction"), _active(items, now, "synonym_card")
            if correction: _correction(draw, correction, width, height)
            if synonym: _synonym_card(draw, synonym, width, height)
            process.stdin.write(np.asarray(image, dtype=np.uint8).tobytes())
        process.stdin.close(); stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        if process.wait(timeout=20 * 60) != 0: raise LanguageReviewOverlayError(stderr[-2000:] or "Language overlay render failed")
    except (BrokenPipeError, subprocess.TimeoutExpired) as exc:
        process.kill(); raise LanguageReviewOverlayError("Language overlay alpha render failed") from exc
    return output


def build_language_review_overlay_command(base_video: str, overlay_webm: str, output_video: str) -> list[str]:
    return ["ffmpeg", "-y", "-i", base_video, "-i", overlay_webm, "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[outv]", "-map", "[outv]", "-map", "0:a?", "-c:v", "libx264", "-preset", "fast", "-c:a", "copy", "-movflags", "+faststart", output_video]
