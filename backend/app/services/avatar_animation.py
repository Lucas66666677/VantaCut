"""Provider-neutral Audio2Face blendshape and MediaPipe-to-rig IK contracts."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings


class AvatarAnimationError(RuntimeError):
    pass


class Audio2FaceProvider(ABC):
    @abstractmethod
    def generate_blendshapes(self, audio_path: Path) -> dict[str, Any]: ...


class GatewayAudio2FaceProvider(Audio2FaceProvider):
    """Adapter for a self-hosted NVIDIA Audio2Face-compatible gateway.

    Contract: POST /blendshapes multipart audio -> {fps, frames:[{time, weights:{ARKitName:0..1}}]}.
    Keeping the gateway private avoids binding API semantics directly into editor workers.
    """
    def generate_blendshapes(self, audio_path: Path) -> dict[str, Any]:
        if not settings.audio2face_gateway_url:
            raise AvatarAnimationError("AUDIO2FACE_GATEWAY_URL is not configured")
        with audio_path.open("rb") as handle:
            response = httpx.post(f"{settings.audio2face_gateway_url.rstrip('/')}/blendshapes", files={"audio": (audio_path.name, handle, "audio/wav")}, timeout=300)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("frames"), list):
            raise AvatarAnimationError("Audio2Face gateway returned an invalid blendshape document")
        return {"provider": "audio2face_gateway", **result}


class MockAudio2FaceProvider(Audio2FaceProvider):
    """Development-only viseme proxy from RMS energy; never present this as neural face animation."""
    def generate_blendshapes(self, audio_path: Path) -> dict[str, Any]:
        try:
            import librosa
            import numpy as np
        except ImportError as exc:
            raise AvatarAnimationError("librosa is required for mock Audio2Face") from exc
        samples, sample_rate = librosa.load(str(audio_path), sr=16000, mono=True)
        hop = 320; rms = librosa.feature.rms(y=samples, frame_length=640, hop_length=hop)[0]
        peak = max(float(np.max(rms)), 1e-6)
        return {"provider": "mock_energy_viseme", "fps": sample_rate / hop, "frames": [
            {"time": round(index * hop / sample_rate, 4), "weights": {"jawOpen": round(float(value / peak), 4), "mouthClose": round(float(1 - value / peak), 4)} }
            for index, value in enumerate(rms)
        ]}


def get_audio2face_provider() -> Audio2FaceProvider:
    return GatewayAudio2FaceProvider() if settings.avatar_audio_provider.lower() in {"audio2face", "nvidia"} else MockAudio2FaceProvider()


def _angle(origin: tuple[float, float], target: tuple[float, float]) -> float:
    from math import atan2, degrees
    return degrees(atan2(target[1] - origin[1], target[0] - origin[0]))


def extract_pose_ik(video_path: Path, *, sample_fps: float = 30.0) -> dict[str, Any]:
    """Map observable 2D pose into a portable humanoid rig animation document.

    The output carries shoulder/elbow angles (two-bone IK targets) and face-driven head yaw/pitch/roll proxies.
    Depth/occluded joints are omitted instead of hallucinated.
    """
    try:
        import cv2
        import mediapipe as mp
    except ImportError as exc:
        raise AvatarAnimationError("OpenCV and MediaPipe are required for avatar motion capture") from exc
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened(): raise AvatarAnimationError("Unable to read source video")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0; stride = max(1, round(fps / sample_fps)); index = 0; frames = []
    pose = mp.solutions.pose.Pose(static_image_mode=False, model_complexity=1, smooth_landmarks=True)
    face = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=1, refine_landmarks=True)
    try:
        while True:
            ok, frame = capture.read()
            if not ok: break
            if index % stride: index += 1; continue
            result, face_result = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), face.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            rig: dict[str, Any] = {"time": round(index / fps, 4), "bones": {}}
            if result.pose_landmarks:
                points = result.pose_landmarks.landmark
                for side, shoulder, elbow, wrist in (("left", 11, 13, 15), ("right", 12, 14, 16)):
                    s, e, w = points[shoulder], points[elbow], points[wrist]
                    if min(s.visibility, e.visibility, w.visibility) >= .45:
                        rig["bones"][f"{side}_upper_arm"] = {"rotation_z": round(_angle((s.x, s.y), (e.x, e.y)), 2)}
                        rig["bones"][f"{side}_lower_arm"] = {"rotation_z": round(_angle((e.x, e.y), (w.x, w.y)), 2), "ik_target": [round(w.x, 4), round(w.y, 4), round(w.z, 4)]}
                hips = ((points[23].x + points[24].x) / 2, (points[23].y + points[24].y) / 2); shoulders = ((points[11].x + points[12].x) / 2, (points[11].y + points[12].y) / 2)
                rig["bones"]["spine"] = {"rotation_z": round(_angle(hips, shoulders) + 90, 2)}
            if face_result.multi_face_landmarks:
                landmarks = face_result.multi_face_landmarks[0].landmark; left, right, nose = landmarks[33], landmarks[263], landmarks[1]
                eye_mid_x, eye_mid_y = (left.x + right.x) / 2, (left.y + right.y) / 2
                rig["bones"]["head"] = {"yaw": round((nose.x - eye_mid_x) * 180, 2), "pitch": round((nose.y - eye_mid_y) * 180, 2), "roll": round(_angle((left.x, left.y), (right.x, right.y)), 2)}
            if rig["bones"]: frames.append(rig)
            index += 1
    finally:
        capture.release(); pose.close(); face.close()
    return {"format": "aivideo.avatar.rig.v1", "fps": round(fps / stride, 3), "frames": frames}


def write_animation_document(destination: Path, *, blendshapes: dict[str, Any], motion: dict[str, Any], rig_mapping: dict[str, Any]) -> Path:
    destination.write_text(json.dumps({"blendshapes": blendshapes, "motion": motion, "rig_mapping": rig_mapping}, ensure_ascii=False), encoding="utf-8")
    return destination
