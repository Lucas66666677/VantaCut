"""COLMAP pose recovery plus configurable 3D Gaussian Splatting training/render adapters."""
from __future__ import annotations

import json
import math
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings


ProgressCallback = Callable[[float, str], None]


class SpatialReconstructionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CameraPose:
    image_name: str
    frame_index: int
    world_to_camera: list[list[float]]
    camera_to_world: list[list[float]]


@dataclass(frozen=True)
class ReconstructionArtifacts:
    dense_point_cloud: Path
    splat_ply: Path
    poses_json: Path
    frame_count: int
    registered_pose_count: int


def _run(command: list[str], *, timeout: int = 4 * 60 * 60) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SpatialReconstructionError(f"Timed out: {' '.join(command[:2])}") from exc
    except subprocess.CalledProcessError as exc:
        raise SpatialReconstructionError((exc.stderr or exc.stdout or "Spatial command failed")[-3000:]) from exc


def _command_template(template: str | None, required: set[str], values: dict[str, Any], label: str) -> list[str]:
    if not template or not required.issubset(set(re.findall(r"\{[^}]+\}", template))):
        raise SpatialReconstructionError(f"{label} must contain {', '.join(sorted(required))}")
    return shlex.split(template.format(**{key: str(value) for key, value in values.items()}))


def extract_video_frames(video_path: Path, images_dir: Path, *, frame_rate: float, max_frames: int) -> int:
    images_dir.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-i", str(video_path), "-vf", f"fps={frame_rate},select='lt(n\\,{max_frames})'", "-vsync", "vfr", "-q:v", "2", str(images_dir / "frame_%06d.jpg")], timeout=2 * 60 * 60)
    count = len(list(images_dir.glob("*.jpg")))
    if count < 30: raise SpatialReconstructionError("At least 30 sharp, overlapping frames are required for spatial reconstruction")
    return count


def _quaternion_rotation(qw: float, qx: float, qy: float, qz: float) -> list[list[float]]:
    norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    if norm <= 1e-8: raise SpatialReconstructionError("COLMAP emitted an invalid zero quaternion")
    qw, qx, qy, qz = qw / norm, qx / norm, qy / norm, qz / norm
    return [[1 - 2*(qy*qy + qz*qz), 2*(qx*qy - qz*qw), 2*(qx*qz + qy*qw)], [2*(qx*qy + qz*qw), 1 - 2*(qx*qx + qz*qz), 2*(qy*qz - qx*qw)], [2*(qx*qz - qy*qw), 2*(qy*qz + qx*qw), 1 - 2*(qx*qx + qy*qy)]]


def _inverse_rigid(matrix: list[list[float]]) -> list[list[float]]:
    rotation = [row[:3] for row in matrix[:3]]; translation = [row[3] for row in matrix[:3]]
    transposed = [[rotation[column][row] for column in range(3)] for row in range(3)]
    inverse_translation = [-sum(transposed[row][column] * translation[column] for column in range(3)) for row in range(3)]
    return [transposed[0] + [inverse_translation[0]], transposed[1] + [inverse_translation[1]], transposed[2] + [inverse_translation[2]], [0, 0, 0, 1]]


def parse_colmap_poses(images_txt: Path) -> list[CameraPose]:
    poses: list[CameraPose] = []
    for line in images_txt.read_text(encoding="utf-8").splitlines():
        columns = line.split()
        if not columns or columns[0].startswith("#") or len(columns) < 10 or not columns[0].isdigit(): continue
        _, qw, qx, qy, qz, tx, ty, tz, _, image_name = columns[:10]
        rotation = _quaternion_rotation(*map(float, (qw, qx, qy, qz)))
        world_to_camera = [rotation[0] + [float(tx)], rotation[1] + [float(ty)], rotation[2] + [float(tz)], [0, 0, 0, 1]]
        match = re.search(r"(\d+)", image_name); frame_index = int(match.group(1)) if match else len(poses)
        poses.append(CameraPose(image_name=image_name, frame_index=frame_index, world_to_camera=world_to_camera, camera_to_world=_inverse_rigid(world_to_camera)))
    if len(poses) < 20: raise SpatialReconstructionError("COLMAP registered too few camera poses; capture needs more parallax and stable texture")
    return poses


def run_colmap(video_path: Path, workspace: Path, *, frame_rate: float, max_frames: int, progress: ProgressCallback | None = None) -> tuple[Path, list[CameraPose], int]:
    command = settings.colmap_command; images, database, sparse, dense, sparse_text = workspace / "images", workspace / "database.db", workspace / "sparse", workspace / "dense", workspace / "sparse-text"
    frame_count = extract_video_frames(video_path, images, frame_rate=frame_rate, max_frames=max_frames)
    if progress: progress(.08, "spatial_frames_extracted")
    _run([command, "feature_extractor", "--database_path", str(database), "--image_path", str(images), "--ImageReader.camera_model", "OPENCV", "--SiftExtraction.use_gpu", "1"])
    if progress: progress(.20, "spatial_features")
    _run([command, "sequential_matcher", "--database_path", str(database), "--SequentialMatching.overlap", "10", "--SiftMatching.guided_matching", "1"])
    if progress: progress(.34, "spatial_matching")
    _run([command, "mapper", "--database_path", str(database), "--image_path", str(images), "--output_path", str(sparse)])
    models = sorted(path for path in sparse.iterdir() if path.is_dir()) if sparse.exists() else []
    if not models: raise SpatialReconstructionError("COLMAP could not build a sparse model; avoid motion blur, reflections, and pure rotations")
    model = models[0]
    _run([command, "model_converter", "--input_path", str(model), "--output_path", str(sparse_text), "--output_type", "TXT"])
    poses = parse_colmap_poses(sparse_text / "images.txt")
    if progress: progress(.48, "spatial_poses")
    _run([command, "image_undistorter", "--image_path", str(images), "--input_path", str(model), "--output_path", str(dense), "--output_type", "COLMAP"])
    _run([command, "patch_match_stereo", "--workspace_path", str(dense), "--workspace_format", "COLMAP", "--PatchMatchStereo.geom_consistency", "1"])
    if progress: progress(.66, "spatial_dense_depth")
    fused = dense / "fused.ply"; _run([command, "stereo_fusion", "--workspace_path", str(dense), "--workspace_format", "COLMAP", "--output_path", str(fused)])
    if not fused.exists(): raise SpatialReconstructionError("COLMAP dense fusion did not output fused.ply")
    return fused, poses, frame_count


def run_3dgs_training(colmap_workspace: Path, output_dir: Path, *, iterations: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = _command_template(settings.three_dgs_train_command, {"{input}", "{output}", "{iterations}"}, {"input": colmap_workspace, "output": output_dir, "iterations": iterations}, "THREE_DGS_TRAIN_COMMAND")
    _run(command, timeout=settings.spatial_training_timeout_seconds)
    candidates = sorted(output_dir.glob("point_cloud/iteration_*/point_cloud.ply"), key=lambda path: path.stat().st_mtime)
    if not candidates: raise SpatialReconstructionError("3DGS trainer completed without a point_cloud/iteration_*/point_cloud.ply artifact")
    return candidates[-1]


def reconstruct_scene(video_path: Path, workspace: Path, *, frame_rate: float, max_frames: int, iterations: int, progress: ProgressCallback | None = None) -> ReconstructionArtifacts:
    colmap_root = workspace / "colmap"; dense, poses, frame_count = run_colmap(video_path, colmap_root, frame_rate=frame_rate, max_frames=max_frames, progress=progress)
    poses_json = workspace / "camera-poses.json"; poses_json.write_text(json.dumps({"coordinate_system": "COLMAP camera-to-world", "poses": [asdict(pose) for pose in poses]}, ensure_ascii=False), encoding="utf-8")
    if progress: progress(.72, "spatial_3dgs_training")
    splat = run_3dgs_training(colmap_root, workspace / "3dgs", iterations=iterations)
    if progress: progress(.93, "spatial_3dgs_trained")
    return ReconstructionArtifacts(dense_point_cloud=dense, splat_ply=splat, poses_json=poses_json, frame_count=frame_count, registered_pose_count=len(poses))


def render_virtual_camera(*, splat_path: Path, camera_path: list[dict[str, Any]], output_path: Path, fps: int, width: int, height: int) -> None:
    camera_json = output_path.with_suffix(".camera-path.json"); camera_json.write_text(json.dumps({"keyframes": camera_path}, ensure_ascii=False), encoding="utf-8")
    command = _command_template(settings.three_dgs_render_command, {"{scene}", "{camera_path}", "{output}", "{fps}", "{width}", "{height}"}, {"scene": splat_path, "camera_path": camera_json, "output": output_path, "fps": fps, "width": width, "height": height}, "THREE_DGS_RENDER_COMMAND")
    _run(command, timeout=settings.spatial_render_timeout_seconds)
    if not output_path.exists(): raise SpatialReconstructionError("3DGS virtual-camera renderer did not create an output video")
