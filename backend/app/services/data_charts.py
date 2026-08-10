"""Time-series chart generation: portable Lottie artifact plus lossless RGBA compositor input."""
from __future__ import annotations

import json
import math
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from app.core.config import settings


class DataChartError(RuntimeError):
    pass


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        raise DataChartError("Chart color must be a six-digit hex value")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _normalise_points(points: list[dict[str, Any]], width: int, height: int, padding: int) -> list[tuple[float, float]]:
    values = [float(point["value"]) for point in points]
    low, high = min(values), max(values)
    span = max(high - low, 1e-6)
    usable_width, usable_height = width - padding * 2, height - padding * 2
    return [
        (padding + usable_width * index / max(1, len(points) - 1), height - padding - (value - low) / span * usable_height)
        for index, value in enumerate(values)
    ]


def _lottie_shape(vertices: list[tuple[float, float]]) -> dict[str, Any]:
    return {"i": [[0, 0] for _ in vertices], "o": [[0, 0] for _ in vertices], "v": [[round(x, 3), round(y, 3)] for x, y in vertices], "c": False}


def build_lottie_chart(chart: dict[str, Any]) -> dict[str, Any]:
    """Build a compact Lottie 5 JSON line-chart animation with progressively revealed path vertices."""
    width, height, fps = int(chart["width"]), int(chart["height"]), int(chart["fps"])
    points = list(chart["points"])
    vertices = _normalise_points(points, width, height, int(chart.get("padding", 36)))
    color = _hex_to_rgb(str(chart.get("color", "#38BDF8")))
    duration_frames = max(1, round((float(chart["end_time"]) - float(chart["start_time"])) * fps))
    keyframes: list[dict[str, Any]] = []
    for index in range(1, len(vertices) + 1):
        keyframes.append({"t": round((index - 1) / max(1, len(vertices) - 1) * duration_frames), "s": [_lottie_shape(vertices[:index])]})
    line_layer = {
        "ddd": 0, "ind": 1, "ty": 4, "nm": "Data line", "sr": 1, "ks": {"o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "p": {"a": 0, "k": [0, 0, 0]}, "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]}},
        "shapes": [{"ty": "gr", "it": [{"ty": "sh", "ks": {"a": 1, "k": keyframes}, "nm": "Animated trend path"}, {"ty": "st", "c": {"a": 0, "k": [color[0] / 255, color[1] / 255, color[2] / 255, 1]}, "o": {"a": 0, "k": 100}, "w": {"a": 0, "k": 4}, "lc": 2, "lj": 2, "nm": "Trend stroke"}, {"ty": "tr", "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": 0}, "o": {"a": 0, "k": 100}}], "nm": "Chart group"}],
        "ip": 0, "op": duration_frames + 1, "st": 0, "bm": 0,
    }
    title = str(chart.get("title", "Market trend"))
    text_layer = {
        "ddd": 0, "ind": 2, "ty": 5, "nm": "Chart title", "sr": 1,
        "ks": {"o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "p": {"a": 0, "k": [36, 28, 0]}, "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]}},
        "t": {
            "d": {"k": [{"s": {"sz": [width - 72, 32], "ps": [0, 0], "s": 22, "f": "Arial", "t": title, "j": 0, "tr": 0, "lh": 26, "fc": [1, 1, 1]}}]},
            "p": {}, "m": {"g": 1, "a": {"a": 0, "k": [0, 0]}},
        },
        "ip": 0, "op": duration_frames + 1, "st": 0, "bm": 0,
    }
    return {"v": "5.7.4", "fr": fps, "ip": 0, "op": duration_frames + 1, "w": width, "h": height, "nm": title, "ddd": 0, "assets": [], "layers": [line_layer, text_layer], "markers": []}


def write_lottie_bundle(chart: dict[str, Any], destination_json: str | Path, destination_lottie: str | Path) -> tuple[Path, Path]:
    payload = build_lottie_chart(chart)
    json_path, lottie_path = Path(destination_json), Path(destination_lottie)
    json_path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    with zipfile.ZipFile(lottie_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps({"version": "1.0", "animations": [{"id": "chart", "loop": False, "speed": 1, "themeColor": chart.get("color", "#38BDF8")}]}))
        archive.writestr("animations/chart.json", json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return json_path, lottie_path


def render_chart_rgba_video(chart: dict[str, Any], output_path: str | Path) -> Path:
    """Reference renderer for an alpha chart movie. Production may replace this with an rlottie/resvg adapter."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise DataChartError("OpenCV and NumPy are required for chart frame rendering") from exc
    width, height, fps = int(chart["width"]), int(chart["height"]), int(chart["fps"])
    duration = float(chart["end_time"]) - float(chart["start_time"])
    if duration <= 0:
        raise DataChartError("Chart end_time must be after start_time")
    points = list(chart["points"])
    vertices = _normalise_points(points, width, height, int(chart.get("padding", 36)))
    line_color = _hex_to_rgb(str(chart.get("color", "#38BDF8")))
    frame_count = max(1, round(duration * fps))
    command = ["ffmpeg", "-y", "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0", "-c:v", "qtrle", "-pix_fmt", "argb", str(output_path)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for frame_index in range(frame_count):
            progress = (frame_index + 1) / frame_count
            visible = max(1, math.ceil(progress * len(vertices)))
            rgba = np.zeros((height, width, 4), dtype=np.uint8)
            cv2.rectangle(rgba, (0, 0), (width - 1, height - 1), (20, 28, 42, 205), thickness=-1)
            cv2.rectangle(rgba, (0, 0), (width - 1, height - 1), (71, 85, 105, 220), thickness=1)
            for grid in range(1, 5):
                y = int(height * grid / 5)
                cv2.line(rgba, (36, y), (width - 20, y), (71, 85, 105, 110), 1)
            cv2.polylines(rgba, [np.asarray(vertices[:visible], dtype=np.int32)], False, (*line_color, 255), 3, cv2.LINE_AA)
            current = points[visible - 1]
            last_x, last_y = map(int, vertices[visible - 1])
            cv2.circle(rgba, (last_x, last_y), 5, (*line_color, 255), thickness=-1, lineType=cv2.LINE_AA)
            cv2.putText(rgba, str(chart.get("title", "Market trend")), (36, 28), cv2.FONT_HERSHEY_SIMPLEX, .65, (255, 255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(rgba, f"{float(current['value']):,.2f}", (36, height - 16), cv2.FONT_HERSHEY_SIMPLEX, .65, (*line_color, 255), 1, cv2.LINE_AA)
            if process.stdin is None:
                raise DataChartError("FFmpeg raw-video input could not be opened")
            process.stdin.write(rgba.tobytes())
    finally:
        if process.stdin:
            process.stdin.close()
    stderr = process.stderr.read() if process.stderr is not None else b""
    return_code = process.wait()
    if return_code:
        raise DataChartError(f"Lossless RGBA chart encoding failed: {stderr.decode(errors='replace')[-1000:]}")
    return Path(output_path)


def render_lottie_or_reference(chart: dict[str, Any], lottie_json_path: str | Path, output_path: str | Path) -> str:
    """Use a production Lottie renderer when configured; otherwise retain deterministic alpha-frame fallback."""
    if settings.lottie_render_command:
        command = settings.lottie_render_command.format(
            input=str(lottie_json_path), output=str(output_path), width=int(chart["width"]),
            height=int(chart["height"]), fps=int(chart["fps"]), duration=float(chart["end_time"]) - float(chart["start_time"]),
        )
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30 * 60)
        if result.returncode or not Path(output_path).exists():
            raise DataChartError(f"Configured Lottie renderer failed: {(result.stderr or result.stdout or '')[-1000:]}")
        return "configured_lottie_renderer"
    render_chart_rgba_video(chart, output_path)
    return "opencv_rgba_reference"


def build_chart_overlay_command(input_path: str, chart_overlays: list[dict[str, Any]], output_path: str) -> list[str]:
    """Composite RGBA chart streams at timeline-relative starts, retaining a lossless FFV1 intermediate."""
    command = ["ffmpeg", "-y", "-i", input_path]
    for overlay in chart_overlays:
        command.extend(["-i", str(overlay["local_path"])])
    filters: list[str] = []
    current = "0:v"
    for index, overlay in enumerate(chart_overlays, start=1):
        output = f"chartcomp{index}"
        start = float(overlay["start_time"])
        x, y = float(overlay.get("x", .04)), float(overlay.get("y", .06))
        filters.append(f"[{index}:v]setpts=PTS-STARTPTS+{start:.6f}/TB[chart{index}]")
        filters.append(f"[{current}][chart{index}]overlay=x=(W-w)*{x:.6f}:y=(H-h)*{y:.6f}:eof_action=pass:format=auto[{output}]")
        current = output
    if not chart_overlays:
        raise DataChartError("At least one chart overlay is required")
    return command + ["-filter_complex", ";".join(filters), "-map", f"[{current}]", "-map", "0:a?", "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv444p", "-c:a", "pcm_s16le", output_path]
