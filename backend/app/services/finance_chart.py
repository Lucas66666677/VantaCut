"""Transparent animated candlestick renderer; annotations are rasterized in the same layer as the chart."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


class FinanceChartError(RuntimeError):
    pass


def _font(size: int) -> ImageFont.ImageFont:
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"):
        if Path(path).exists(): return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _bezier(point: dict[str, Any], t: float) -> tuple[float, float]:
    p0, p1, p2, p3 = (point[key] for key in ("p0", "p1", "p2", "p3")); u = 1 - t
    return (u**3*p0["x"] + 3*u*u*t*p1["x"] + 3*u*t*t*p2["x"] + t**3*p3["x"], u**3*p0["y"] + 3*u*u*t*p1["y"] + 3*u*t*t*p2["y"] + t**3*p3["y"])


def _draw_annotations(draw: ImageDraw.ImageDraw, annotations: list[dict[str, Any]], bounds: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = bounds
    for annotation in annotations:
        color = (92, 240, 158, 245) if annotation.get("kind") == "support" else (255, 110, 120, 245)
        points = [_bezier(annotation, index / 32) for index in range(33)]
        pixel_points = [(left + x * (right - left), top + y * (bottom - top)) for x, y in points]
        draw.line(pixel_points, fill=color, width=4)
        draw.text(pixel_points[-1], str(annotation.get("label", annotation.get("kind", ""))), font=_font(16), anchor="rs", fill=color, stroke_width=1, stroke_fill=(8, 12, 20, 230))


def render_finance_chart_rgba(track: dict[str, Any], output_path: str | Path) -> Path:
    candles, width, height, fps = list(track["candles"]), int(track["width"]), int(track["height"]), int(track["fps"])
    duration = float(track["end_time"]) - float(track["start_time"])
    if not candles or duration <= 0: raise FinanceChartError("Candles and positive Timeline duration are required")
    output = Path(output_path); chart_bounds = (52, 52, width - 32, int(height * .72)); prices = [float(candle[key]) for candle in candles for key in ("high", "low")]
    low, high = min(prices), max(prices); span = max(high - low, 1e-6); left, top, right, bottom = chart_bounds
    command = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "-", "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for frame in range(math.ceil(duration * fps)):
            progress = min(1.0, (frame / fps) / max(duration * .72, .1)); visible = max(1, math.ceil(len(candles) * progress))
            image = Image.new("RGBA", (width, height), (8, 12, 20, 215)); draw = ImageDraw.Draw(image); draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=18, outline=(80, 130, 180, 220), width=2)
            draw.text((24, 20), f"{track['symbol']} · {track['market'].upper()} · {track.get('data_as_of', '')}", font=_font(20), fill=(220, 235, 255, 255))
            for grid in range(5):
                y = top + (bottom - top) * grid / 4; value = high - span * grid / 4
                draw.line((left, y, right, y), fill=(110, 135, 165, 70), width=1); draw.text((right, y), f"{value:.2f}", font=_font(13), anchor="ls", fill=(180, 200, 220, 210))
            gap, candle_width = (right - left) / max(len(candles), 1), max(2, int((right - left) / max(len(candles), 1) * .58))
            y_of = lambda value: bottom - (float(value) - low) / span * (bottom - top)
            for index, candle in enumerate(candles[:visible]):
                x = left + (index + .5) * gap; open_y, close_y, high_y, low_y = y_of(candle["open"]), y_of(candle["close"]), y_of(candle["high"]), y_of(candle["low"]); color = (52, 221, 153, 255) if candle["close"] >= candle["open"] else (255, 92, 104, 255)
                draw.line((x, high_y, x, low_y), fill=color, width=2); draw.rectangle((x - candle_width / 2, min(open_y, close_y), x + candle_width / 2, max(open_y, close_y, min(open_y, close_y) + 1)), fill=color)
            _draw_annotations(draw, list(track.get("annotations", [])), chart_bounds)
            latest = candles[min(visible - 1, len(candles) - 1)].get("indicators", {}); rsi = latest.get("rsi14"); macd = latest.get("macd")
            draw.text((left, int(height * .81)), f"RSI(14): {'—' if rsi is None else f'{rsi:.1f}'}    MACD: {'—' if macd is None else f'{macd:.3f}'}", font=_font(17), fill=(240, 198, 110, 255))
            draw.text((left, int(height * .9)), "Market data for education/visualisation only · not investment advice", font=_font(13), fill=(170, 185, 205, 220))
            process.stdin.write(np.asarray(image, dtype=np.uint8).tobytes())
        process.stdin.close(); stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        if process.wait(timeout=30 * 60) != 0: raise FinanceChartError(stderr[-1800:] or "Finance chart render failed")
    except (BrokenPipeError, subprocess.TimeoutExpired) as exc:
        process.kill(); raise FinanceChartError("Finance chart alpha rendering failed") from exc
    return output
