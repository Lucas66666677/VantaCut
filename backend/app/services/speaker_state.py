"""Explainable speaker delivery metrics from face, iris, pose, and temporal motion signals."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.facs import FACSActionUnitEstimator


class SpeakerStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeakerSegment:
    id: str
    source_start: float
    source_end: float


def _distance(left: Any, right: Any) -> float:
    return float(((left.x - right.x) ** 2 + (left.y - right.y) ** 2) ** 0.5)


def _mean_landmarks(landmarks: list[Any]) -> tuple[float, float]:
    return sum(item.x for item in landmarks) / len(landmarks), sum(item.y for item in landmarks) / len(landmarks)


def _eye_aspect_ratio(points: list[Any]) -> float:
    horizontal = max(_distance(points[0], points[3]), 1e-6)
    return (_distance(points[1], points[5]) + _distance(points[2], points[4])) / (2 * horizontal)


def _mean(values: list[float], default: float = 0.0) -> float:
    return sum(values) / len(values) if values else default


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def analyze_speaker_delivery(
    video_path: str | Path, segments: list[SpeakerSegment], *, sample_fps: float = 6.0
) -> list[dict[str, Any]]:
    """Calculate segment-level delivery metrics. Scores are advisory, not a biometric identity claim."""
    try:
        import cv2
        import mediapipe as mp
    except ImportError as exc:
        raise SpeakerStateError("OpenCV and MediaPipe are required for speaker analysis") from exc
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise SpeakerStateError("Unable to open video for speaker analysis")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    stride = max(1, round(fps / max(sample_fps, 1)))
    observations: list[dict[str, float]] = []
    previous_wrists: tuple[tuple[float, float], tuple[float, float]] | None = None
    frame_index = 0
    face_mesh = mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False, max_num_faces=1, refine_landmarks=True,
        min_detection_confidence=0.55, min_tracking_confidence=0.55,
    )
    pose = mp.solutions.pose.Pose(
        static_image_mode=False, model_complexity=1, smooth_landmarks=True,
        min_detection_confidence=0.55, min_tracking_confidence=0.55,
    )
    facs = FACSActionUnitEstimator()
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % stride:
                frame_index += 1
                continue
            timestamp = frame_index / fps
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            face_result, pose_result = face_mesh.process(rgb), pose.process(rgb)
            observation: dict[str, float] = {"timestamp": timestamp}
            if face_result.multi_face_landmarks:
                face = face_result.multi_face_landmarks[0].landmark
                # Refined Face Mesh iris landmarks are 468..477; score iris-centre displacement within eye corners.
                left_iris = _mean_landmarks([face[index] for index in range(468, 473)])
                right_iris = _mean_landmarks([face[index] for index in range(473, 478)])
                left_eye, right_eye = (face[33], face[133]), (face[362], face[263])
                left_ratio = (left_iris[0] - min(left_eye[0].x, left_eye[1].x)) / max(abs(left_eye[0].x - left_eye[1].x), 1e-6)
                right_ratio = (right_iris[0] - min(right_eye[0].x, right_eye[1].x)) / max(abs(right_eye[0].x - right_eye[1].x), 1e-6)
                eye_contact = _clamp(1 - (abs(left_ratio - .5) + abs(right_ratio - .5)))
                eye_center = ((left_eye[0].x + left_eye[1].x + right_eye[0].x + right_eye[1].x) / 4, (left_eye[0].y + left_eye[1].y + right_eye[0].y + right_eye[1].y) / 4)
                eye_span = max(abs(left_eye[0].x - right_eye[0].x), 1e-6)
                horizontal_alignment = _clamp(1 - abs(face[1].x - eye_center[0]) / eye_span * 3.0)
                # Face Mesh z is relative camera depth: asymmetric eye depth indicates a turned face.
                depth_alignment = _clamp(1 - abs(left_eye[0].z - right_eye[0].z) / eye_span * 4.0)
                head_alignment = min(horizontal_alignment, depth_alignment)
                left_ear = _eye_aspect_ratio([face[index] for index in (33, 160, 158, 133, 153, 144)])
                right_ear = _eye_aspect_ratio([face[index] for index in (362, 385, 387, 263, 373, 380)])
                mouth_open = _distance(face[13], face[14]) / eye_span
                brow_motion_proxy = abs(face[65].y - face[159].y) + abs(face[295].y - face[386].y)
                observation.update({
                    "eye_contact": eye_contact, "head_alignment": head_alignment, "head_depth_alignment": depth_alignment,
                    "eye_open": _clamp((left_ear + right_ear) / .55),
                    "expression_energy": _clamp(mouth_open * 4 + brow_motion_proxy * 2),
                })
                if facs.available:
                    height, width = frame.shape[:2]
                    xs, ys = [point.x for point in face], [point.y for point in face]
                    x0, x1 = max(0, int(min(xs) * width)), min(width, int(max(xs) * width))
                    y0, y1 = max(0, int(min(ys) * height)), min(height, int(max(ys) * height))
                    action_units = facs.predict(frame[y0:y1, x0:x1])
                    if action_units:
                        observation.update(action_units)
            if pose_result.pose_landmarks:
                points = pose_result.pose_landmarks.landmark
                left_shoulder, right_shoulder, left_wrist, right_wrist = points[11], points[12], points[15], points[16]
                shoulder_width = max(_distance(left_shoulder, right_shoulder), 1e-6)
                shoulder_center = ((left_shoulder.x + right_shoulder.x) / 2, (left_shoulder.y + right_shoulder.y) / 2)
                gesture_amplitude = _clamp((_distance(left_wrist, left_shoulder) + _distance(right_wrist, right_shoulder)) / (shoulder_width * 3))
                wrist_pair = ((left_wrist.x, left_wrist.y), (right_wrist.x, right_wrist.y))
                motion = 0.0 if previous_wrists is None else _clamp((sum((wrist_pair[i][j] - previous_wrists[i][j]) ** 2 for i in range(2) for j in range(2)) ** .5) / shoulder_width * 3)
                previous_wrists = wrist_pair
                shoulder_tilt = abs(left_shoulder.y - right_shoulder.y) / shoulder_width
                observation.update({
                    "gesture_amplitude": gesture_amplitude, "gesture_motion": motion,
                    "posture_openness": _clamp(1 - shoulder_tilt * 2),
                    "shoulder_tilt": shoulder_tilt,
                    "gesture_openness": _clamp(_distance(left_wrist, right_wrist) / (shoulder_width * 2.2)),
                })
            observations.append(observation)
            frame_index += 1
    finally:
        capture.release()
        face_mesh.close()
        pose.close()

    result: list[dict[str, Any]] = []
    for segment in segments:
        samples = [item for item in observations if segment.source_start <= item["timestamp"] <= segment.source_end]
        face_samples = [item for item in samples if "eye_contact" in item]
        pose_samples = [item for item in samples if "posture_openness" in item]
        assessment_status = "assessed" if face_samples else "insufficient_visual_evidence"
        eye = _mean([item.get("eye_contact", 0) for item in samples])
        head = _mean([item.get("head_alignment", 0) for item in samples])
        head_depth = _mean([item.get("head_depth_alignment", 0) for item in samples])
        posture = _mean([item.get("posture_openness", 0) for item in samples])
        gesture = _mean([item.get("gesture_amplitude", 0) for item in samples])
        gesture_motion = _mean([item.get("gesture_motion", 0) for item in samples])
        expression = _mean([item.get("expression_energy", 0) for item in samples])
        gesture_open = _mean([item.get("gesture_openness", 0) for item in samples])
        gaze_away_rate = _mean([1.0 if item.get("eye_contact", 1) < .5 else 0.0 for item in face_samples])
        eye_closure_rate = _mean([1.0 if item.get("eye_open", 1) < .36 else 0.0 for item in face_samples])
        blink_events = sum(
            1 for previous, current in zip(face_samples, face_samples[1:])
            if previous.get("eye_open", 1) >= .36 and current.get("eye_open", 1) < .36
        )
        blink_rate = blink_events * 60 / max(.1, segment.source_end - segment.source_start)
        shoulder_stability = 1 - min(1, _mean([abs(item.get("shoulder_tilt", 0) - _mean([sample.get("shoulder_tilt", 0) for sample in pose_samples])) for item in pose_samples]) * 12)
        body_rigidity = _clamp(1 - min(1, gesture_motion * 1.8 + gesture * .7) * .75 - shoulder_stability * .25)
        facs_samples = [item for item in face_samples if "au04_brow_lowerer" in item]
        facial_tension = _mean([item.get("au04_brow_lowerer", 0) + item.get("au20_lip_stretcher", 0) for item in facs_samples]) / 2
        eye_stability = 1 - min(1, _mean([abs(item.get("eye_contact", eye) - eye) for item in samples]) * 3)
        confidence = round(_clamp(eye * .45 + head * .25 + posture * .2 + min(gesture * 1.4, 1) * .1) * 100)
        fluency = round(_clamp(eye_stability * .45 + min(gesture_motion * 2.2, 1) * .3 + min(expression * 1.5, 1) * .25) * 100)
        suggestions: list[str] = []
        if not face_samples:
            suggestions.append("未能穩定辨識講者臉部；請以人工審閱取代自動呈現評估。")
        elif eye < .55:
            suggestions.append("眼神未穩定朝向鏡頭；建議重拍，或以 B-Roll 覆蓋此段。")
        if face_samples and head < .52:
            suggestions.append("頭部明顯偏離鏡頭；可調整提詞器或鏡頭位置後重錄。")
        if pose_samples and posture < .5:
            suggestions.append("肢體姿態較封閉或傾斜；建議放鬆肩膀並面向鏡頭。")
        if pose_samples and gesture < .18 and segment.source_end - segment.source_start >= 3:
            suggestions.append("手勢幅度偏低；可加入自然手勢或插入示意 B-Roll。")
        if face_samples and fluency < 55:
            suggestions.append("表情／手勢節奏不夠流暢；建議縮短句子並分段錄製。")
        if gaze_away_rate > .35:
            suggestions.append("此段有較多視線離開鏡頭的畫面；可將提詞器靠近鏡頭，或重錄關鍵句。")
        if face_samples and blink_rate > 28:
            suggestions.append("此段偵測到較密集的眼睛閉合／眨眼訊號；請人工確認是否影響觀感，可縮短停頓或改用 B-Roll 覆蓋。")
        if pose_samples and body_rigidity > .68:
            suggestions.append("肢體動態偏低且姿態變化有限；可放鬆肩膀、加入自然手勢，或以示意 B-Roll 增加畫面節奏。")
        if facs_samples and facial_tension > .62:
            suggestions.append("偵測到較高的臉部緊繃動作單元活化；請以個人舒適感自行確認，建議先停頓與放慢呼吸後重錄。")
        result.append({
            "segment_id": segment.id, "source_start": segment.source_start, "source_end": segment.source_end,
            "assessment_status": assessment_status,
            "confidence_score": confidence, "fluency_score": fluency,
            "metrics": {"eye_contact": round(eye * 100), "gaze_away_rate": round(gaze_away_rate * 100), "blink_rate_per_min": round(blink_rate, 1), "eye_closure_rate": round(eye_closure_rate * 100), "head_alignment": round(head * 100), "head_depth_alignment": round(head_depth * 100), "posture_openness": round(posture * 100), "gesture_amplitude": round(gesture * 100), "gesture_openness": round(gesture_open * 100), "gesture_motion": round(gesture_motion * 100), "body_rigidity_proxy": round(body_rigidity * 100), "expression_energy": round(expression * 100), "facs_available": int(bool(facs_samples)), "facial_tension_proxy": round(facial_tension * 100) if facs_samples else None, "sample_count": len(samples), "face_sample_count": len(face_samples), "pose_sample_count": len(pose_samples)},
            "suggestions": suggestions,
        })
    return result
