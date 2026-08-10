"""Zero-shot component observations, flow-derived mechanical cues, and safe source-code alignment."""
from __future__ import annotations

import ast
import math
from pathlib import Path
from typing import Any

from app.core.config import settings


class MechanicalARError(RuntimeError):
    pass


def parse_program_actions(source: str, extension: str) -> dict[str, Any]:
    """Parse only source structure; uploaded code is never executed by a Worker."""
    if extension == ".hex":
        records = sum(1 for line in source.splitlines() if line.strip().startswith(":"))
        return {
            "language": "intel_hex", "actions": [],
            "notice": f"Intel HEX has {records} records but no source-level action names; upload Python source or provide manual markers for line highlights.",
        }
    try:
        root = ast.parse(source)
    except SyntaxError as exc:
        raise MechanicalARError(f"Python source could not be parsed: {exc.msg} on line {exc.lineno}") from exc
    actions: list[dict[str, Any]] = []
    action_terms = {"forward", "backward", "reverse", "turn", "rotate", "run", "start", "stop", "move", "drive", "servo", "motor"}
    for node in ast.walk(root):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr.lower()
        elif isinstance(node.func, ast.Name):
            name = node.func.id.lower()
        else:
            continue
        if not any(term in name for term in action_terms):
            continue
        line_start, line_end = int(node.lineno), int(getattr(node, "end_lineno", node.lineno))
        snippet = "\n".join(source.splitlines()[line_start - 1:line_end]).strip()
        actions.append({"action": name, "line_start": line_start, "line_end": line_end, "snippet": snippet[:280]})
    return {"language": "python", "actions": sorted(actions, key=lambda item: item["line_start"]), "notice": None}


def _load_yolo_world(vocabulary: list[str]) -> Any:
    if not settings.mechanical_yolo_world_model:
        raise MechanicalARError("MECHANICAL_YOLO_WORLD_MODEL must point to a pre-provisioned YOLO-World weight")
    model_path = Path(settings.mechanical_yolo_world_model)
    if not model_path.exists():
        raise MechanicalARError("Configured YOLO-World model weight is not present on this Worker")
    try:
        from ultralytics import YOLOWorld
    except ImportError as exc:
        raise MechanicalARError("ultralytics with YOLO-World support is required for zero-shot part recognition") from exc
    model = YOLOWorld(str(model_path))
    model.set_classes(vocabulary)
    return model


def _normalised_bbox(x1: float, y1: float, x2: float, y2: float, width: int, height: int) -> dict[str, float]:
    return {
        "x": round(max(0.0, x1 / width), 5), "y": round(max(0.0, y1 / height), 5),
        "width": round(max(0.0, (x2 - x1) / width), 5), "height": round(max(0.0, (y2 - y1) / height), 5),
    }


def _detect(model: Any, frame: Any) -> list[dict[str, Any]]:
    height, width = frame.shape[:2]
    result = model.predict(frame, conf=settings.mechanical_detection_confidence, verbose=False)[0]
    names = result.names
    detected: list[dict[str, Any]] = []
    for box in result.boxes:
        x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
        class_id = int(box.cls[0].item())
        detected.append({"label": str(names[class_id]), "confidence": round(float(box.conf[0].item()), 4), "bbox_norm": _normalised_bbox(x1, y1, x2, y2, width, height), "bbox_px": (x1, y1, x2, y2)})
    return detected


def _flow_features(flow: Any, bbox: tuple[float, float, float, float]) -> dict[str, float]:
    import numpy as np

    x1, y1, x2, y2 = (int(value) for value in bbox)
    height, width = flow.shape[:2]; x1, x2 = max(0, x1), min(width, x2); y1, y2 = max(0, y1), min(height, y2)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return {"magnitude": 0.0, "dx": 0.0, "dy": 0.0, "angular_velocity": 0.0}
    crop = flow[y1:y2, x1:x2]; dx, dy = float(crop[..., 0].mean()), float(crop[..., 1].mean())
    yy, xx = np.mgrid[y1:y2, x1:x2]; cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    radius_squared = (xx - cx) ** 2 + (yy - cy) ** 2 + 1.0
    angular = float((((xx - cx) * crop[..., 1] - (yy - cy) * crop[..., 0]) / radius_squared).mean())
    return {"magnitude": round(float(np.sqrt(crop[..., 0] ** 2 + crop[..., 1] ** 2).mean()), 4), "dx": round(dx, 4), "dy": round(dy, 4), "angular_velocity": round(angular, 5)}


def analyze_mechanical_video(video_path: str | Path, *, vocabulary: list[str], sample_fps: float) -> dict[str, Any]:
    """Run frozen-vocabulary YOLO-World observations plus Farneback flow at a bounded cadence."""
    try:
        import cv2
    except ImportError as exc:
        raise MechanicalARError("OpenCV is required for mechanical AR analysis") from exc
    model = _load_yolo_world(vocabulary)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise MechanicalARError("Unable to open source video for mechanical analysis")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    frame_interval = max(1, round(fps / sample_fps)); observations: list[dict[str, Any]] = []; effects: list[dict[str, Any]] = []
    previous_gray: Any | None = None; frame_index = 0; sampled = 0
    try:
        while sampled < settings.mechanical_max_sampled_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % frame_interval:
                frame_index += 1; continue
            timestamp = frame_index / fps; gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            flow = cv2.calcOpticalFlowFarneback(previous_gray, gray, None, .5, 4, 21, 5, 7, 1.5, 0) if previous_gray is not None else None
            for item in _detect(model, frame):
                feature = _flow_features(flow, item["bbox_px"]) if flow is not None else {"magnitude": 0.0, "dx": 0.0, "dy": 0.0, "angular_velocity": 0.0}
                observation = {**{key: value for key, value in item.items() if key != "bbox_px"}, "source_time": round(timestamp, 3), "motion": feature}
                observations.append(observation)
                label = item["label"].lower()
                if "gear" in label and feature["magnitude"] >= .35:
                    effects.append({"type": "gear_rotation", "source_start": max(0, timestamp - .10), "source_end": timestamp + .75, "bbox_norm": item["bbox_norm"], "angular_velocity": feature["angular_velocity"], "confidence": min(item["confidence"], min(1.0, feature["magnitude"] / 3))})
                elif ("linkage" in label or "motor" in label or "servo" in label) and feature["magnitude"] >= .35:
                    effects.append({"type": "motion_vector", "source_start": max(0, timestamp - .10), "source_end": timestamp + .75, "bbox_norm": item["bbox_norm"], "dx": feature["dx"], "dy": feature["dy"], "confidence": min(item["confidence"], min(1.0, feature["magnitude"] / 3))})
                elif "wire" in label:
                    effects.append({"type": "illustrative_signal_flow", "source_start": timestamp, "source_end": timestamp + .75, "bbox_norm": item["bbox_norm"], "confidence": item["confidence"], "notice": "Illustrative signal path inferred from visible wiring; it is not an electrical measurement."})
            previous_gray = gray; frame_index += 1; sampled += 1
    finally:
        capture.release()
    return {"status": "completed", "sample_fps": sample_fps, "sample_count": sampled, "part_observations": observations, "visual_effects": effects, "recognition_notice": "Zero-shot labels are visual hypotheses. Confirm component identity and wiring before instructional export."}


def source_to_output_time(segments: list[dict[str, Any]], source_time: float) -> float | None:
    output_cursor = 0.0
    for segment in segments:
        if segment.get("action", "keep") != "keep":
            continue
        start, end = float(segment["source_start"]), float(segment["source_end"])
        if start <= source_time <= end:
            return round(output_cursor + source_time - start, 3)
        output_cursor += end - start
    return None


def project_effects_to_timeline(effects: list[dict[str, Any]], segments: list[dict[str, Any]], code_actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    visible_motion = [item for item in effects if item["type"] in {"gear_rotation", "motion_vector"}]
    for item in effects:
        start, end = source_to_output_time(segments, float(item["source_start"])), source_to_output_time(segments, float(item["source_end"]))
        if start is not None and end is not None:
            projected.append({**item, "output_start": start, "output_end": max(start + .1, end)})
    for index, action in enumerate(code_actions):
        target = visible_motion[min(index, len(visible_motion) - 1)] if visible_motion else None
        if target is None:
            continue
        start = source_to_output_time(segments, float(target["source_start"]))
        if start is None:
            continue
        projected.append({"type": "code_highlight", "output_start": start, "output_end": round(start + 2.5, 3), "line_start": action["line_start"], "line_end": action["line_end"], "snippet": action["snippet"], "action": action["action"], "confidence": target["confidence"], "reason": "Visual motion event was aligned sequentially to a source-code motor/action call; confirm before publish."})
    return projected
