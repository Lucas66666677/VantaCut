"""SAM 2 prompted video matting with CLIP proposal ranking and temporal alpha refinement."""
from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.ai.providers.factory import get_embedding_provider
from app.core.config import settings


ProgressCallback = Callable[[float, str], None]


class VideoMattingError(RuntimeError):
    pass


@dataclass(frozen=True)
class MattingFrame:
    frame_index: int
    timestamp: float
    alpha_path: str
    rgba_path: str
    bbox: tuple[int, int, int, int]
    confidence: float


class SAM2VideoMattingProvider:
    """Thin adapter around Meta's official SAM 2 video predictor API."""

    @property
    def name(self) -> str:
        return "sam2"

    @staticmethod
    def _require_checkpoint() -> str:
        if not settings.sam2_checkpoint_path:
            raise VideoMattingError("SAM2_CHECKPOINT_PATH must point to a provisioned SAM 2 checkpoint")
        return settings.sam2_checkpoint_path

    def _video_predictor(self) -> Any:
        try:
            from sam2.build_sam import build_sam2_video_predictor
        except ImportError as exc:
            raise VideoMattingError("SAM 2 is not installed; install backend/requirements.sam2.txt on the GPU worker") from exc
        return build_sam2_video_predictor(settings.sam2_config_path, self._require_checkpoint())

    def _image_model(self) -> Any:
        try:
            from sam2.build_sam import build_sam2
        except ImportError as exc:
            raise VideoMattingError("SAM 2 is not installed; install backend/requirements.sam2.txt on the GPU worker") from exc
        return build_sam2(settings.sam2_config_path, self._require_checkpoint(), device="cuda")

    @staticmethod
    def _write_logits(mask_logits: Any, path: Path) -> None:
        import cv2
        import numpy as np

        logits = mask_logits.detach().float().cpu().numpy() if hasattr(mask_logits, "detach") else np.asarray(mask_logits)
        binary = (np.squeeze(logits) > 0).astype(np.uint8) * 255
        if not cv2.imwrite(str(path), binary):
            raise VideoMattingError(f"Unable to write SAM 2 mask: {path}")

    def _propagate(self, predictor: Any, state: Any, output_dir: Path, progress: ProgressCallback | None) -> dict[int, Path]:
        masks: dict[int, Path] = {}
        output_dir.mkdir(parents=True, exist_ok=True)
        for count, (frame_idx, object_ids, mask_logits) in enumerate(predictor.propagate_in_video(state), start=1):
            object_position = list(object_ids).index(1) if 1 in list(object_ids) else 0
            path = output_dir / f"{int(frame_idx):06d}.png"
            self._write_logits(mask_logits[object_position], path)
            masks[int(frame_idx)] = path
            if progress and count % 8 == 0:
                progress(min(.92, .08 + count / (count + 80)), "sam2_propagating")
        if not masks:
            raise VideoMattingError("SAM 2 did not return any propagated masks")
        return masks

    def track_from_clicks(
        self, video_path: Path, *, frame_index: int, points: list[dict[str, Any]], output_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> dict[int, Path]:
        import numpy as np

        predictor = self._video_predictor()
        state = predictor.init_state(video_path=str(video_path))
        coordinates, labels = [], []
        width, height = video_dimensions(video_path)
        for point in points:
            coordinates.append([float(point["x"]) * width, float(point["y"]) * height])
            labels.append(1 if bool(point.get("positive", True)) else 0)
        predictor.add_new_points_or_box(
            inference_state=state, frame_idx=frame_index, obj_id=1,
            points=np.asarray(coordinates, dtype=np.float32), labels=np.asarray(labels, dtype=np.int32),
        )
        return self._propagate(predictor, state, output_dir, progress)

    def track_from_initial_mask(
        self, video_path: Path, *, frame_index: int, initial_mask: Any, output_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> dict[int, Path]:
        predictor = self._video_predictor()
        state = predictor.init_state(video_path=str(video_path))
        if not hasattr(predictor, "add_new_mask"):
            raise VideoMattingError("Installed SAM 2 predictor does not support mask prompts")
        predictor.add_new_mask(inference_state=state, frame_idx=frame_index, obj_id=1, mask=initial_mask)
        return self._propagate(predictor, state, output_dir, progress)

    def text_prompt_mask(self, frame_bgr: Any, prompt: str, workdir: Path) -> Any:
        """Generate SAM regions, rank them in CLIP space, then use the best region as a video prompt."""
        import cv2
        import numpy as np
        from PIL import Image

        try:
            from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
        except ImportError as exc:
            raise VideoMattingError("SAM 2 automatic mask generator is unavailable") from exc
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        workdir.mkdir(parents=True, exist_ok=True)
        proposals = SAM2AutomaticMaskGenerator(self._image_model()).generate(rgb)
        if not proposals:
            raise VideoMattingError("SAM 2 generated no semantic mask proposals")
        provider = get_embedding_provider()
        query = provider.embed_text(prompt)
        best_score, best_mask = -math.inf, None
        for index, proposal in enumerate(proposals[:160]):
            mask = np.asarray(proposal["segmentation"], dtype=bool)
            x, y, width, height = proposal["bbox"]
            if width * height < rgb.shape[0] * rgb.shape[1] * .002:
                continue
            crop = rgb[int(y):int(y + height), int(x):int(x + width)]
            if crop.size == 0:
                continue
            crop_path = workdir / f"proposal-{index:04d}.jpg"
            Image.fromarray(crop).save(crop_path, quality=88)
            vector = provider.embed_image(str(crop_path))
            similarity = sum(left * right for left, right in zip(query, vector))
            # Prefer a region with a stable SAM score when semantic similarity is close.
            score = similarity + float(proposal.get("predicted_iou", 0)) * .08
            if score > best_score:
                best_score, best_mask = score, mask
        if best_mask is None:
            raise VideoMattingError(f"No usable SAM proposal matched text prompt: {prompt}")
        return best_mask.astype(np.uint8)


def video_dimensions(video_path: Path) -> tuple[int, int]:
    import cv2

    capture = cv2.VideoCapture(str(video_path))
    try:
        width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        capture.release()
    if width < 2 or height < 2:
        raise VideoMattingError("Unable to determine video dimensions")
    return width, height


def reference_frame(video_path: Path, frame_index: int) -> Any:
    import cv2

    capture = cv2.VideoCapture(str(video_path))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        raise VideoMattingError("Unable to decode requested reference frame")
    return frame


def _bbox(alpha: Any) -> tuple[int, int, int, int]:
    import cv2

    points = cv2.findNonZero((alpha > 16).astype("uint8"))
    return tuple(map(int, cv2.boundingRect(points))) if points is not None else (0, 0, 0, 0)


def _despill(bgr: Any, alpha: Any, strength: float) -> Any:
    import numpy as np

    result = bgr.astype(np.float32)
    blue, green, red = result[..., 0], result[..., 1], result[..., 2]
    edge = np.clip(1 - np.abs(alpha.astype(np.float32) / 255 - .5) * 2, 0, 1)
    green_spill = np.maximum(0, green - np.maximum(red, blue))
    green -= green_spill * edge * strength
    return np.clip(result, 0, 255).astype(np.uint8)


def refine_matte_sequence(
    video_path: Path,
    masks: dict[int, Path],
    *,
    output_dir: Path,
    feather_pixels: float,
    despill_strength: float,
    progress: ProgressCallback | None = None,
) -> tuple[list[MattingFrame], float]:
    """Feather edges, flow-smooth alpha temporally, despill RGB, and write premultiplied RGBA PNGs."""
    import cv2
    import numpy as np

    alpha_dir, rgba_dir = output_dir / "alpha", output_dir / "rgba"
    alpha_dir.mkdir(parents=True, exist_ok=True); rgba_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    previous_gray, previous_alpha = None, None
    frames: list[MattingFrame] = []
    index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            mask_path = masks.get(index)
            if mask_path is None:
                index += 1
                continue
            mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
            if mask is None:
                raise VideoMattingError(f"Unable to read SAM mask for frame {index}")
            kernel = max(1, int(round(feather_pixels * 2)) * 2 + 1)
            alpha = cv2.GaussianBlur(mask, (kernel, kernel), feather_pixels or 0.1)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if previous_gray is not None and previous_alpha is not None:
                flow = cv2.calcOpticalFlowFarneback(previous_gray, gray, None, .5, 3, 19, 3, 5, 1.2, 0)
                height, width = alpha.shape
                grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
                warped = cv2.remap(previous_alpha, grid_x - flow[..., 0], grid_y - flow[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
                alpha = cv2.addWeighted(alpha, .72, warped, .28, 0)
            cleaned = _despill(frame, alpha, despill_strength)
            rgba = cv2.cvtColor(cleaned, cv2.COLOR_BGR2BGRA); rgba[..., 3] = alpha
            alpha_path, rgba_path = alpha_dir / f"{index:06d}.png", rgba_dir / f"{index:06d}.png"
            cv2.imwrite(str(alpha_path), alpha); cv2.imwrite(str(rgba_path), rgba)
            frames.append(MattingFrame(index, round(index / fps, 4), str(alpha_path), str(rgba_path), _bbox(alpha), 1.0))
            previous_gray, previous_alpha = gray, alpha
            if progress and index % 12 == 0:
                progress(.1 + .9 * index / max(1, total), "matte_refining")
            index += 1
    finally:
        capture.release()
    if not frames:
        raise VideoMattingError("No alpha frames were produced")
    return frames, fps


def render_alpha_webm(rgba_dir: Path, fps: float, output_path: Path) -> None:
    command = [
        "ffmpeg", "-y", "-framerate", f"{fps:.6f}", "-i", str(rgba_dir / "%06d.png"),
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", "30", str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=2 * 60 * 60)
    except subprocess.TimeoutExpired as exc:
        raise VideoMattingError("Alpha WebM render timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise VideoMattingError((exc.stderr or "Alpha WebM render failed")[-2000:]) from exc


def save_matte_manifest(path: Path, frames: list[MattingFrame], *, provider: str, mode: str, source_fps: float) -> None:
    path.write_text(json.dumps({
        "version": 1, "provider": provider, "mode": mode, "source_fps": source_fps,
        "frames": [{**asdict(frame), "alpha_name": Path(frame.alpha_path).name, "rgba_name": Path(frame.rgba_path).name} for frame in frames],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
