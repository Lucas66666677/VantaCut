"""Depth-aware 2.5D parallax layer generation and FFmpeg graph compilation.

This is deliberately a layered screen-space effect: monocular depth cannot reveal pixels
hidden behind the foreground, so the background plate uses temporal inpainting rather than
claiming to reconstruct a true 3D scene.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.schemas.keyframes import ClipTransformAnimation
from app.services.keyframe_animation import FFmpegKeyframeCompiler
from app.services.virtual_relighting import RelativeDepthEstimator, _dependencies


class ParallaxZoomError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParallaxLayerArtifacts:
    background_video: Path
    foreground_alpha_video: Path
    frame_count: int
    fps: float
    width: int
    height: int


def foreground_alpha_from_depth(relative_depth: Any, *, foreground_quantile: float = .62, feather_pixels: float = 4.0) -> Any:
    """Turn relative depth into a stable soft foreground alpha matte.

    Most monocular models emit larger values for nearer regions after the repository's
    normalisation. A per-frame quantile keeps a person in front even when exposure changes.
    """
    cv2, np = _dependencies()
    threshold = float(np.quantile(relative_depth, foreground_quantile))
    alpha = (relative_depth >= threshold).astype(np.uint8) * 255
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    alpha = cv2.GaussianBlur(alpha, (0, 0), max(.1, feather_pixels))
    return alpha


def generate_parallax_layers(
    input_path: str | Path,
    output_directory: str | Path,
    *,
    depth_model: str = "auto",
    temporal_smoothing: float = .78,
    foreground_quantile: float = .62,
    feather_pixels: float = 4.0,
) -> ParallaxLayerArtifacts:
    """Split a video into a inpainted background plate and a transparent foreground layer."""
    cv2, np = _dependencies()
    output = Path(output_directory)
    foreground_frames = output / "foreground"
    output.mkdir(parents=True, exist_ok=True)
    foreground_frames.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise ParallaxZoomError("Unable to decode source video for parallax layers")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    background_path = output / "background.mp4"
    writer = cv2.VideoWriter(str(background_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    estimator = RelativeDepthEstimator(depth_model)
    previous_depth = previous_alpha = None
    frame_count = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            depth = estimator.estimate(frame)
            if previous_depth is not None:
                depth = temporal_smoothing * previous_depth + (1 - temporal_smoothing) * depth
            alpha = foreground_alpha_from_depth(depth, foreground_quantile=foreground_quantile, feather_pixels=feather_pixels)
            if previous_alpha is not None:
                alpha = cv2.addWeighted(alpha, 1 - temporal_smoothing, previous_alpha, temporal_smoothing, 0).astype(np.uint8)
            previous_depth, previous_alpha = depth, alpha
            # Telea fills the person-shaped hole from nearby background; this is a pragmatic plate for parallax,
            # not generative reconstruction of occluded scenery.
            background = cv2.inpaint(frame, (alpha > 128).astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
            writer.write(background)
            rgba = np.dstack((frame, alpha))
            cv2.imwrite(str(foreground_frames / f"foreground-{frame_count:06d}.png"), rgba)
            frame_count += 1
    finally:
        capture.release()
        writer.release()
    if not frame_count:
        raise ParallaxZoomError("Source video contained no frames")
    foreground_video = output / "foreground-alpha.webm"
    result = subprocess.run([
        "ffmpeg", "-y", "-framerate", f"{fps:.6f}", "-i", str(foreground_frames / "foreground-%06d.png"),
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", str(foreground_video),
    ], capture_output=True, text=True, timeout=60 * 60)
    if result.returncode:
        raise ParallaxZoomError(f"Could not encode alpha foreground: {result.stderr[-1200:]}")
    return ParallaxLayerArtifacts(background_path, foreground_video, frame_count, fps, width, height)


def build_parallax_filtergraph(
    animation: ClipTransformAnimation,
    *,
    background_input: str = "1:v",
    foreground_input: str = "2:v",
    width: int,
    height: int,
    fps: float = 30.0,
    foreground_depth_strength: float = 1.0,
    background_depth_strength: float = .28,
    output_label: str = "parallaxv",
) -> str:
    """Return a graph fragment: slow background movement plus stronger foreground camera push.

    `zoompan` uses the same Bézier-sampled timing as ordinary keyframes. Separate animation
    scales preserve the illusion that foreground objects travel farther than the background.
    """
    if foreground_depth_strength <= background_depth_strength or background_depth_strength < 0:
        raise ParallaxZoomError("Foreground parallax strength must exceed non-negative background strength")
    compiler = FFmpegKeyframeCompiler(animation, fps=fps)
    values = compiler.compile()
    # Authored Scale frames conventional framing; Camera Z separately introduces the larger
    # foreground displacement that makes this feel like a physical push-in.
    foreground_zoom = f"max(0.1,({values.zoom})+({values.camera_z})*{foreground_depth_strength:.5f})"
    background_zoom = f"max(0.1,({values.zoom})+({values.camera_z})*{background_depth_strength:.5f})"
    x = values.x
    y = values.y
    return ";".join([
        f"[{background_input}]zoompan=z='{background_zoom}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps:.8f}[parallaxbg]",
        f"[{foreground_input}]format=rgba,zoompan=z='{foreground_zoom}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps:.8f},format=rgba[parallaxfg]",
        f"[parallaxbg][parallaxfg]overlay=x=0:y=0:shortest=1[{output_label}]",
    ])
