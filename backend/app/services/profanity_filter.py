"""Word-timestamp profanity detection, Face Mesh mouth anchors, and safe replacement audio."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


PROFANITY_DICTIONARY = frozenset({
    "fuck", "fucking", "shit", "bitch", "asshole", "damn", "wtf",
    "幹", "靠北", "靠", "媽的", "他媽的", "白癡", "智障", "王八蛋",
})


def _normalise(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def detect_profanity_words(cues: list[dict[str, Any]], *, padding_seconds: float = .035) -> list[dict[str, Any]]:
    """Return exact ASR word timings; phrase matching remains deterministic and auditable."""
    events: list[dict[str, Any]] = []
    for cue in cues:
        for word_index, word in enumerate(cue.get("words", [])):
            if not isinstance(word, dict):
                continue
            token = _normalise(str(word.get("word", "")))
            if token not in PROFANITY_DICTIONARY:
                continue
            start, end = float(word.get("start", cue.get("start_time", 0))), float(word.get("end", cue.get("end_time", 0)))
            if end <= start:
                continue
            events.append({"id": f"profanity-{cue.get('id', 'cue')}-{word_index}", "word": str(word.get("word", "")), "start_time": round(max(0.0, start - padding_seconds), 3), "end_time": round(end + padding_seconds, 3), "confidence": float(word.get("confidence", 1.0)), "reason": "Matched profanity dictionary against ASR word timestamp."})
    return events


def map_output_events_to_source(events: list[dict[str, Any]], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map final timeline time back to source time for Face Mesh analysis of one source proxy."""
    cursor = 0.0; mapped: list[dict[str, Any]] = []
    keep = [item for item in segments if item.get("action", "keep") == "keep"]
    for event in events:
        event_start, event_end = float(event["start_time"]), float(event["end_time"])
        local_cursor = cursor; match = None
        for segment in keep:
            duration = float(segment.get("source_end", 0)) - float(segment.get("source_start", 0))
            if local_cursor <= event_start < local_cursor + duration:
                match = (segment, local_cursor, duration); break
            local_cursor += max(0.0, duration)
        if match is None:
            mapped.append({**event, "source_start": event_start, "source_end": event_end}); continue
        segment, start, duration = match
        source_start = float(segment["source_start"]) + event_start - start
        source_end = min(float(segment["source_end"]), source_start + min(event_end - event_start, duration))
        mapped.append({**event, "source_start": round(source_start, 3), "source_end": round(source_end, 3)})
    return mapped


def track_mouth_positions(video_path: str | Path, events: list[dict[str, Any]], *, sample_fps: float = 10.0) -> list[dict[str, Any]]:
    """Use MediaPipe Face Mesh lip landmarks; a documented centre fallback keeps renders deterministic."""
    try:
        import cv2
        import mediapipe as mp
    except ImportError:
        return [{**event, "mouth_position": {"x": .5, "y": .63, "scale": .14, "tracking": "fallback"}} for event in events]
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return [{**event, "mouth_position": {"x": .5, "y": .63, "scale": .14, "tracking": "fallback"}} for event in events]
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0; step = max(1, round(fps / sample_fps)); results: list[dict[str, Any]] = []
    mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=1, refine_landmarks=True, min_detection_confidence=.55, min_tracking_confidence=.55)
    try:
        for event in events:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(event["source_start"]) * 1000)
            positions: list[tuple[float, float, float]] = []; frames = max(1, round((float(event["source_end"]) - float(event["source_start"])) * fps / step))
            for _ in range(frames):
                ok, frame = capture.read()
                if not ok: break
                face = mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).multi_face_landmarks
                if face:
                    landmarks = face[0].landmark; left, right, upper, lower = landmarks[61], landmarks[291], landmarks[13], landmarks[14]
                    positions.append(((left.x + right.x) / 2, (upper.y + lower.y) / 2, max(.08, min(.26, abs(right.x - left.x) * 1.85))))
                for _ in range(step - 1): capture.grab()
            if positions:
                x = sum(item[0] for item in positions) / len(positions); y = sum(item[1] for item in positions) / len(positions); scale = sum(item[2] for item in positions) / len(positions)
                results.append({**event, "mouth_position": {"x": round(x, 4), "y": round(y, 4), "scale": round(scale, 4), "tracking": "face_mesh"}})
            else:
                results.append({**event, "mouth_position": {"x": .5, "y": .63, "scale": .14, "tracking": "fallback"}})
    finally:
        mesh.close(); capture.release()
    return results


def build_profanity_mix_command(*, video_path: str, output_path: str, events: list[dict[str, Any]], style: str) -> list[str]:
    frequencies = {"beep": 1000.0, "chicken": 630.0, "coin": 1320.0}
    frequency = frequencies.get(style, 1000.0); command = ["ffmpeg", "-y", "-i", video_path]; filters = ["[0:a]asetpts=PTS-STARTPTS[basea]"]; current = "basea"
    for index, event in enumerate(events):
        start, end = float(event["start_time"]), float(event["end_time"]); duration = max(.06, end - start); delay = round(start * 1000)
        command.extend(["-f", "lavfi", "-i", f"sine=frequency={frequency:.1f}:sample_rate=48000:duration={duration:.3f}"])
        muted, replacement, mixed = f"profanitymute{index}", f"profanitysfx{index}", f"profanitymix{index}"
        filters.append(f"[{current}]volume=volume=0:enable='between(t\\,{start:.6f}\\,{end:.6f})'[{muted}]")
        filters.append(f"[{index + 1}:a]volume=.82,afade=t=in:st=0:d=.01,afade=t=out:st={max(.01, duration - .03):.4f}:d=.03,adelay={delay}:all=1[{replacement}]")
        filters.append(f"[{muted}][{replacement}]amix=inputs=2:duration=first:normalize=0[{mixed}]")
        current = mixed
    return command + ["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", f"[{current}]", "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", output_path]


def generate_censor_sticker_png(style: str, output_path: str | Path) -> None:
    """Create a portable transparent angry-face/duck sticker without relying on OS emoji fonts."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0)); draw = ImageDraw.Draw(image)
    if style == "duck":
        draw.ellipse((26, 28, 230, 228), fill=(255, 220, 55, 255), outline=(65, 48, 18, 255), width=10)
        draw.ellipse((67, 86, 91, 112), fill=(25, 25, 25, 255)); draw.ellipse((165, 86, 189, 112), fill=(25, 25, 25, 255))
        draw.rounded_rectangle((70, 130, 186, 180), radius=24, fill=(255, 135, 42, 255), outline=(92, 46, 15, 255), width=7)
    else:
        draw.ellipse((22, 22, 234, 234), fill=(245, 69, 69, 255), outline=(80, 18, 18, 255), width=10)
        draw.line((60, 91, 104, 108), fill=(45, 12, 12, 255), width=14); draw.line((196, 91, 152, 108), fill=(45, 12, 12, 255), width=14)
        draw.ellipse((74, 108, 96, 132), fill=(20, 12, 12, 255)); draw.ellipse((160, 108, 182, 132), fill=(20, 12, 12, 255))
        draw.arc((71, 125, 185, 204), 200, 340, fill=(48, 12, 12, 255), width=13)
        draw.polygon(((104, 22), (128, -10), (143, 31), (169, -4), (174, 39)), fill=(255, 220, 70, 255))
    image.save(output_path, "PNG")
