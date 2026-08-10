from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from app.services.virtual_relighting import RelativeDepthEstimator, _dependencies

class SpatialTextError(RuntimeError): pass

def solve_spatial_tracking(video_path: Path, workdir: Path) -> tuple[Path, Path, int, float]:
    cv2, np = _dependencies(); capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened(): raise SpatialTextError("Unable to decode video")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0; width, height = int(capture.get(3)), int(capture.get(4))
    depth_path, poses_path = workdir / "depth.mp4", workdir / "camera-poses.json"
    writer = cv2.VideoWriter(str(depth_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)); estimator = RelativeDepthEstimator("auto")
    previous_gray = None
    # A calibrated intrinsics approximation is sufficient for camera *motion*
    # recovery from consumer footage. Translation remains relative-scale because
    # monocular video has no absolute metric baseline.
    focal = float(max(width, height)); camera_matrix = np.array([[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]], dtype=np.float64)
    camera_to_world = np.eye(4, dtype=np.float64); poses: list[dict[str, Any]] = []; index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok: break
            depth = estimator.estimate(frame); normalized = cv2.normalize(depth, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8); writer.write(cv2.cvtColor(normalized, cv2.COLOR_GRAY2BGR))
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if previous_gray is not None:
                points = cv2.goodFeaturesToTrack(previous_gray, maxCorners=300, qualityLevel=.01, minDistance=8)
                if points is not None:
                    moved, status, _ = cv2.calcOpticalFlowPyrLK(previous_gray, gray, points, None)
                    source, target = points[status.ravel() == 1], moved[status.ravel() == 1]
                    if len(source) >= 8:
                        try:
                            essential, inliers = cv2.findEssentialMat(source, target, camera_matrix, method=cv2.RANSAC, prob=.999, threshold=1.0)
                            if essential is not None:
                                _, rotation, translation, _ = cv2.recoverPose(essential, source, target, camera_matrix, mask=inliers)
                                relative_motion = np.eye(4, dtype=np.float64); relative_motion[:3, :3] = rotation; relative_motion[:3, 3] = translation.reshape(3)
                                # recoverPose returns current-camera motion relative to the
                                # previous camera. Inverting yields a camera-to-world path.
                                camera_to_world = camera_to_world @ np.linalg.inv(relative_motion)
                        except cv2.error:
                            # Low-texture / pure-rotation shots can lack a stable essential
                            # matrix; retain the last trusted pose rather than jittering text.
                            pass
            poses.append({"frame": index, "time": round(index / fps, 4), "matrix": camera_to_world.round(7).tolist(), "translation_scale": "relative_monocular"}); previous_gray = gray; index += 1
    finally: capture.release(); writer.release()
    if not index: raise SpatialTextError("Video has no frames")
    poses_path.write_text(json.dumps({"coordinate_system": "camera_to_world_right_handed", "intrinsics": camera_matrix.round(6).tolist(), "translation_scale": "relative_monocular", "poses": poses}, ensure_ascii=False), encoding="utf-8")
    return depth_path, poses_path, index, fps
