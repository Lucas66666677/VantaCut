"""Opt-in ONNX GAN gaze redirector with blink preservation and eye-only compositing."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class GazeRedirectionError(RuntimeError):
    pass


class ONNXGazeGAN:
    """Adapter contract: ONNX model input image NCHW [-1,1], target_gaze [yaw,pitch], output corrected face NCHW."""

    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path or os.getenv("GAZE_REDIRECTION_ONNX_PATH")
        if not self.model_path or not Path(self.model_path).exists():
            raise GazeRedirectionError("GAZE_REDIRECTION_ONNX_PATH must point to a provisioned, consented gaze GAN model")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise GazeRedirectionError("onnxruntime is required for gaze redirection") from exc
        self.session = ort.InferenceSession(self.model_path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        self.inputs = {item.name: item for item in self.session.get_inputs()}

    def correct_face(self, bgr_face: Any, *, yaw: float = 0.0, pitch: float = 0.0) -> Any:
        import cv2
        import numpy as np
        image_name = next((name for name in self.inputs if "image" in name.lower() or "face" in name.lower()), None)
        gaze_name = next((name for name in self.inputs if "gaze" in name.lower() or "angle" in name.lower()), None)
        if image_name is None or gaze_name is None:
            raise GazeRedirectionError("Gaze GAN must expose image/face and gaze/angle inputs")
        height, width = bgr_face.shape[:2]
        resized = cv2.resize(bgr_face, (256, 256), interpolation=cv2.INTER_AREA)
        tensor = (cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 127.5 - 1).transpose(2, 0, 1)[None]
        output = self.session.run(None, {image_name: tensor, gaze_name: np.array([[yaw, pitch]], dtype=np.float32)})[0]
        generated = output[0].transpose(1, 2, 0)
        generated = np.clip((generated + 1) * 127.5, 0, 255).astype(np.uint8)
        return cv2.resize(cv2.cvtColor(generated, cv2.COLOR_RGB2BGR), (width, height), interpolation=cv2.INTER_CUBIC)


def redirect_gaze_video(input_path: str | Path, output_path: str | Path, *, model_path: str | None = None) -> None:
    """Correct only open-eye frames; blink frames remain untouched to preserve natural dynamics."""
    import cv2
    import mediapipe as mp
    import numpy as np

    engine = ONNXGazeGAN(model_path)
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise GazeRedirectionError("Unable to open source video")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    face_mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=1, refine_landmarks=True, min_detection_confidence=.55, min_tracking_confidence=.55)
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            result = face_mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if not result.multi_face_landmarks:
                writer.write(frame)
                continue
            face = result.multi_face_landmarks[0].landmark
            eye_open = (_eye_aspect(face, (33, 160, 158, 133, 153, 144)) + _eye_aspect(face, (362, 385, 387, 263, 373, 380))) / 2
            if eye_open < .16:  # Preserve blink closure exactly rather than GAN-inpaint it.
                writer.write(frame)
                continue
            source_yaw, source_pitch = _head_pose_from_3d_landmarks(face)
            # Avoid needless re-synthesis when the face is already close to the camera axis.
            if abs(source_yaw) < .025 and abs(source_pitch) < .025:
                writer.write(frame)
                continue
            xs, ys = [point.x * width for point in face], [point.y * height for point in face]
            left, right, top, bottom = max(0, int(min(xs) - width * .04)), min(width, int(max(xs) + width * .04)), max(0, int(min(ys) - height * .06)), min(height, int(max(ys) + height * .06))
            if right - left < 32 or bottom - top < 32:
                writer.write(frame)
                continue
            original = frame[top:bottom, left:right]
            generated = engine.correct_face(original)
            eye_points = np.array([(int(face[index].x * width) - left, int(face[index].y * height) - top) for index in (33, 133, 160, 158, 153, 144, 362, 263, 385, 387, 373, 380)], dtype=np.int32)
            mask = np.zeros(original.shape[:2], dtype=np.uint8)
            cv2.fillConvexPoly(mask, cv2.convexHull(eye_points[:6]), 255)
            cv2.fillConvexPoly(mask, cv2.convexHull(eye_points[6:]), 255)
            mask = cv2.GaussianBlur(mask, (21, 21), 0)
            alpha = (mask.astype(np.float32) / 255)[..., None]
            frame[top:bottom, left:right] = (generated * alpha + original * (1 - alpha)).astype(np.uint8)
            writer.write(frame)
    finally:
        capture.release(); writer.release(); face_mesh.close()


def _eye_aspect(face: list[Any], indices: tuple[int, int, int, int, int, int]) -> float:
    def distance(first: Any, second: Any) -> float:
        return ((first.x - second.x) ** 2 + (first.y - second.y) ** 2) ** .5
    points = [face[index] for index in indices]
    return (distance(points[1], points[5]) + distance(points[2], points[4])) / max(2 * distance(points[0], points[3]), 1e-6)


def _head_pose_from_3d_landmarks(face: list[Any]) -> tuple[float, float]:
    """A small 3D Face-Mesh pose proxy used only to decide whether an eye correction is needed."""
    left_eye, right_eye, nose = face[33], face[263], face[1]
    eye_span = max(abs(left_eye.x - right_eye.x), 1e-6)
    yaw = (left_eye.z - right_eye.z) / eye_span
    pitch = (nose.z - (left_eye.z + right_eye.z) / 2) / eye_span
    return float(yaw), float(pitch)
