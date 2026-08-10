"""Screen-recording focus analysis: cursor/OCR evidence -> renderer-neutral pan/zoom effects."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
from typing import Any

from app.core.config import settings


class ScreenFocusError(RuntimeError):
    pass


@dataclass(frozen=True)
class CursorObservation:
    source_time: float
    x: float
    y: float
    width: float
    height: float
    confidence: float


@dataclass(frozen=True)
class OCRRegion:
    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float


def _dependencies() -> tuple[Any, Any, Any]:
    try:
        import cv2
        import numpy as np
        import pytesseract
    except ImportError as exc:
        raise ScreenFocusError("OpenCV, NumPy and pytesseract are required for screen-focus analysis") from exc
    return cv2, np, pytesseract


def _load_cursor_templates() -> list[Any]:
    cv2, np, _ = _dependencies()
    # Built-in edge template for the common white-arrow cursor. Teams can add exact cursor PNGs
    # for their OS/theme through SCREEN_FOCUS_CURSOR_TEMPLATES.
    cursor = np.zeros((28, 22), dtype=np.uint8)
    cv2.fillConvexPoly(cursor, np.array([[2, 1], [2, 22], [8, 16], [12, 26], [16, 24], [11, 14], [20, 14]], dtype=np.int32), 255)
    templates: list[Any] = [cv2.Canny(cursor, 50, 150)]
    for raw_path in settings.screen_focus_cursor_templates:
        image = cv2.imread(raw_path, cv2.IMREAD_GRAYSCALE)
        if image is not None and image.size:
            templates.append(cv2.Canny(image, 50, 150))
    return templates


def _detect_cursor(gray: Any, templates: list[Any], timestamp: float) -> CursorObservation | None:
    cv2, _, _ = _dependencies()
    edges = cv2.Canny(gray, 50, 150)
    best: tuple[float, int, int, int, int] | None = None
    for template in templates:
        height, width = template.shape[:2]
        if height >= gray.shape[0] or width >= gray.shape[1]:
            continue
        _, score, _, location = cv2.minMaxLoc(cv2.matchTemplate(edges, template, cv2.TM_CCORR_NORMED))
        if best is None or score > best[0]:
            best = (float(score), int(location[0]), int(location[1]), width, height)
    if best is None or best[0] < 0.48:
        return None
    score, x, y, width, height = best
    return CursorObservation(timestamp, x + width / 2, y + height / 2, width, height, score)


def _active_window_region(frame: Any) -> dict[str, Any]:
    """Find the largest edge-bounded application region; fullscreen is a safe fallback."""
    cv2, _, _ = _dependencies()
    height, width = frame.shape[:2]
    edges = cv2.Canny(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 60, 160)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = [cv2.boundingRect(contour) for contour in contours]
    candidates = [box for box in candidates if box[2] * box[3] >= width * height * 0.2]
    if not candidates:
        return {"x": 0, "y": 0, "width": width, "height": height, "confidence": 0.35}
    x, y, box_width, box_height = max(candidates, key=lambda item: item[2] * item[3])
    return {"x": int(x), "y": int(y), "width": int(box_width), "height": int(box_height), "confidence": 0.62}


def _ocr_regions(frame: Any, active_region: dict[str, Any]) -> list[OCRRegion]:
    cv2, _, pytesseract = _dependencies()
    x, y, width, height = (int(active_region[key]) for key in ("x", "y", "width", "height"))
    roi = frame[y:y + height, x:x + width]
    if roi.size == 0:
        return []
    # Upscaling makes small editor labels and IDE code considerably more legible to Tesseract.
    enlarged = cv2.resize(roi, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
    data = pytesseract.image_to_data(enlarged, lang=settings.screen_focus_ocr_lang, config="--oem 1 --psm 11", output_type=pytesseract.Output.DICT)
    regions: list[OCRRegion] = []
    for index, raw_text in enumerate(data["text"]):
        text = str(raw_text).strip()
        confidence = float(data["conf"][index]) if str(data["conf"][index]).lstrip("-").replace(".", "", 1).isdigit() else -1
        if not text or confidence < 35:
            continue
        regions.append(OCRRegion(text=text, x=x + int(data["left"][index] / 1.6), y=y + int(data["top"][index] / 1.6), width=max(2, int(data["width"][index] / 1.6)), height=max(2, int(data["height"][index] / 1.6)), confidence=confidence / 100))
    return regions


def _terms(text: str) -> set[str]:
    # Identifiers, component/pin labels and voltage names survive ASR much better than broad natural-language phrases.
    tokens = re.findall(r"\b(?:[A-Za-z_][A-Za-z0-9_\-]*|\d+(?:\.\d+)?[Vv]|GPIO\d+|GND|VCC)\b", text)
    ignored = {"the", "and", "this", "that", "then", "with", "into", "from", "我們", "這個", "把", "接到"}
    return {token.lower() for token in tokens if len(token) >= 2 and token.lower() not in ignored}


def _matching_regions(regions: list[OCRRegion], spoken_text: str) -> list[tuple[OCRRegion, str, float]]:
    matches: list[tuple[OCRRegion, str, float]] = []
    for term in _terms(spoken_text):
        for region in regions:
            normalized = region.text.lower().strip(".,:;()[]{}")
            score = SequenceMatcher(None, term, normalized).ratio()
            if term == normalized:
                score = 1.0
            if score >= 0.78:
                matches.append((region, term, score))
    return matches


def _infer_probable_clicks(cursor_path: list[CursorObservation]) -> list[dict[str, Any]]:
    """Infer visual click-like pauses. It is deliberately labelled probable: mouse button state is not in video pixels."""
    clicks: list[dict[str, Any]] = []
    for before, current, after in zip(cursor_path, cursor_path[1:], cursor_path[2:]):
        before_speed = ((current.x - before.x) ** 2 + (current.y - before.y) ** 2) ** 0.5 / max(0.001, current.source_time - before.source_time)
        after_speed = ((after.x - current.x) ** 2 + (after.y - current.y) ** 2) ** 0.5 / max(0.001, after.source_time - current.source_time)
        if before_speed >= 40 and after_speed <= 10:
            clicks.append({"source_time": round(current.source_time, 3), "x": round(current.x, 1), "y": round(current.y, 1), "kind": "probable_click", "confidence": round(min(0.8, 0.35 + current.confidence * 0.5), 3)})
    return clicks


def _spoken_text_at(cues: list[dict[str, Any]], source_time: float, window_seconds: float = 1.6) -> str:
    return " ".join(str(cue.get("text", "")) for cue in cues if abs(float(cue.get("source_time", -9999)) - source_time) <= window_seconds)


def analyze_screen_recording(video_path: str | Path, *, spoken_cues: list[dict[str, Any]], sample_seconds: float = 0.5) -> dict[str, Any]:
    cv2, _, _ = _dependencies()
    if sample_seconds <= 0:
        raise ValueError("sample_seconds must be positive")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ScreenFocusError("Unable to decode screen-recording video")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    templates = _load_cursor_templates()
    stride = max(1, round(fps * sample_seconds))
    cursor_path: list[CursorObservation] = []
    candidates: list[dict[str, Any]] = []
    active_windows: list[dict[str, Any]] = []
    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % stride:
                frame_index += 1
                continue
            source_time = frame_index / fps
            active = _active_window_region(frame)
            active_windows.append({"source_time": round(source_time, 3), **active})
            cursor = _detect_cursor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), templates, source_time) if templates else None
            if cursor:
                cursor_path.append(cursor)
            spoken = _spoken_text_at(spoken_cues, source_time)
            if spoken:
                for region, term, text_score in _matching_regions(_ocr_regions(frame, active), spoken):
                    candidates.append({"source_time": source_time, "label": term, "spoken_text": spoken, "target_bbox_norm": {"x": region.x / width, "y": region.y / height, "width": region.width / width, "height": region.height / height}, "confidence": round(min(0.99, 0.4 * region.confidence + 0.4 * text_score + 0.2 * active["confidence"]), 3), "evidence": {"ocr_text": region.text, "ocr_confidence": region.confidence, "active_window": active}})
            frame_index += 1
    finally:
        capture.release()
    candidates.sort(key=lambda item: item["source_time"])
    # Suppress repeated OCR detections of the same label in a short spoken explanation.
    events: list[dict[str, Any]] = []
    for candidate in candidates:
        if events and candidate["label"] == events[-1]["label"] and candidate["source_time"] - events[-1]["source_time"] < 1.2:
            if candidate["confidence"] > events[-1]["confidence"]:
                events[-1] = candidate
            continue
        events.append(candidate)
    return {"source_width": width, "source_height": height, "fps": fps, "active_windows": active_windows, "cursor_tracking": {"template_count": len(templates), "observations": [asdict(item) for item in cursor_path], "click_events": _infer_probable_clicks(cursor_path), "limitations": "Click events are inferred from visible cursor motion unless a pressed-cursor template is supplied."}, "focus_candidates": events, "sample_seconds": sample_seconds}


def source_time_to_output(segments: list[dict[str, Any]], source_time: float) -> float | None:
    output_time = 0.0
    for segment in segments:
        if segment.get("action", "keep") != "keep":
            continue
        start, end = float(segment["source_start"]), float(segment["source_end"])
        if start <= source_time <= end:
            return output_time + source_time - start
        output_time += end - start
    return None


def focus_effects_from_candidates(report: dict[str, Any], main_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    for index, candidate in enumerate(report.get("focus_candidates", []), start=1):
        output_time = source_time_to_output(main_segments, float(candidate["source_time"]))
        if output_time is None:
            continue
        bbox = dict(candidate["target_bbox_norm"])
        effects.append({"id": f"screen-focus-{index}", "type": "screen_focus", "output_start": round(max(0, output_time - 0.20), 3), "output_end": round(output_time + 1.8, 3), "zoom": 1.7, "transition_seconds": 0.20, "target_bbox_norm": bbox, "center_norm": {"x": round(float(bbox["x"]) + float(bbox["width"]) / 2, 4), "y": round(float(bbox["y"]) + float(bbox["height"]) / 2, 4)}, "highlight": {"color": "#FACC15", "opacity": 0.35, "padding": 0.08}, "label": candidate["label"], "confidence": candidate["confidence"], "reason": f"OCR matched '{candidate['evidence']['ocr_text']}' to spoken term '{candidate['label']}'."})
    # A click-like pause is secondary evidence. Add it only when it is not adjacent to a stronger OCR/transcript match.
    source_width, source_height = max(1, int(report.get("source_width", 1))), max(1, int(report.get("source_height", 1)))
    for click in report.get("cursor_tracking", {}).get("click_events", []):
        source_time = float(click["source_time"])
        if any(abs(source_time - float(item.get("source_time", -999))) < 0.8 for item in report.get("focus_candidates", [])):
            continue
        output_time = source_time_to_output(main_segments, source_time)
        if output_time is None:
            continue
        side = 0.10
        center_x, center_y = float(click["x"]) / source_width, float(click["y"]) / source_height
        bbox = {"x": round(max(0, center_x - side / 2), 4), "y": round(max(0, center_y - side / 2), 4), "width": side, "height": side}
        effects.append({"id": f"screen-focus-click-{len(effects) + 1}", "type": "screen_focus", "output_start": round(max(0, output_time - 0.15), 3), "output_end": round(output_time + 1.3, 3), "zoom": 1.5, "transition_seconds": 0.18, "target_bbox_norm": bbox, "center_norm": {"x": round(center_x, 4), "y": round(center_y, 4)}, "highlight": {"color": "#FACC15", "opacity": 0.28, "padding": 0.10}, "label": "probable_click", "confidence": float(click["confidence"]), "reason": "Visible cursor trajectory indicates a probable click; verify before publishing."})
    effects.sort(key=lambda item: item["output_start"])
    return effects


def write_screen_focus_sendcmd(effects: list[dict[str, Any]], source_width: int, source_height: int, destination: str | Path, *, crop_filter_name: str = "screen_focus") -> Path:
    """Write eased crop commands in *output* timeline time for FFmpeg's named crop filter."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for effect in effects:
        start, end, zoom = float(effect["output_start"]), float(effect["output_end"]), float(effect.get("zoom", 1.7))
        transition = min(float(effect.get("transition_seconds", 0.2)), max(0.01, (end - start) / 2))
        center = effect["center_norm"]
        crop_width, crop_height = max(2, int(source_width / zoom) // 2 * 2), max(2, int(source_height / zoom) // 2 * 2)
        x = max(0, min(source_width - crop_width, int(float(center["x"]) * source_width - crop_width / 2) // 2 * 2))
        y = max(0, min(source_height - crop_height, int(float(center["y"]) * source_height - crop_height / 2) // 2 * 2))
        for step in range(7):
            progress = step / 6
            eased = progress * progress * (3 - 2 * progress)
            at = start + transition * eased
            width = int(source_width - (source_width - crop_width) * eased) // 2 * 2
            height = int(source_height - (source_height - crop_height) * eased) // 2 * 2
            current_x, current_y = int(x * eased) // 2 * 2, int(y * eased) // 2 * 2
            lines.append(f"{at:.6f} crop@{crop_filter_name} w {max(2, width)}, crop@{crop_filter_name} h {max(2, height)}, crop@{crop_filter_name} x {max(0, current_x)}, crop@{crop_filter_name} y {max(0, current_y)};")
        lines.append(f"{end:.6f} crop@{crop_filter_name} w {source_width // 2 * 2}, crop@{crop_filter_name} h {source_height // 2 * 2}, crop@{crop_filter_name} x 0, crop@{crop_filter_name} y 0;")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
