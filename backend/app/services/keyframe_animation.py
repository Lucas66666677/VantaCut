"""Shared cubic-Bézier evaluator and FFmpeg expression compiler for transform keyframes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.schemas.keyframes import ClipTransformAnimation, CubicBezier, TransformKeyframe


def _cubic(value: float, left: float, right: float) -> float:
    inverse = 1 - value
    return 3 * inverse * inverse * value * left + 3 * inverse * value * value * right + value ** 3


def cubic_bezier_progress(time_progress: float, curve: CubicBezier) -> float:
    """Evaluate CSS-style cubic-bezier(x1,y1,x2,y2) by inverting x with Newton+bisection."""
    target = max(0.0, min(1.0, time_progress))
    parameter = target
    for _ in range(8):
        x = _cubic(parameter, curve.x1, curve.x2)
        derivative = 3 * (1 - parameter) ** 2 * curve.x1 + 6 * (1 - parameter) * parameter * (curve.x2 - curve.x1) + 3 * parameter ** 2 * (1 - curve.x2)
        if abs(derivative) < 1e-7:
            break
        parameter = max(0.0, min(1.0, parameter - (x - target) / derivative))
    low, high = 0.0, 1.0
    for _ in range(12):
        x = _cubic(parameter, curve.x1, curve.x2)
        if x < target:
            low = parameter
        else:
            high = parameter
        parameter = (low + high) / 2
    return _cubic(parameter, curve.y1, curve.y2)


def easing_progress(progress: float, keyframe: TransformKeyframe) -> float:
    if keyframe.easing == "linear":
        return max(0.0, min(1.0, progress))
    if keyframe.easing == "ease-in-out":
        return cubic_bezier_progress(progress, CubicBezier())
    return cubic_bezier_progress(progress, keyframe.cubic_bezier or CubicBezier())


def evaluate(animation: ClipTransformAnimation, time: float, attribute: str) -> float:
    frames = animation.keyframes
    if time <= frames[0].time:
        return float(getattr(frames[0].value, attribute))
    if time >= frames[-1].time:
        return float(getattr(frames[-1].value, attribute))
    for left, right in zip(frames, frames[1:]):
        if left.time <= time <= right.time:
            progress = easing_progress((time - left.time) / (right.time - left.time), left)
            start, end = float(getattr(left.value, attribute)), float(getattr(right.value, attribute))
            return start + (end - start) * progress
    return float(getattr(frames[-1].value, attribute))


@dataclass(frozen=True)
class FFmpegTransformExpressions:
    zoom: str
    camera_z: str
    x: str
    y: str
    rotation_radians: str
    sample_count: int


class FFmpegKeyframeCompiler:
    """Approximates exact browser Bézier interpolation with frame-rate sampled FFmpeg expressions."""

    def __init__(self, animation: ClipTransformAnimation, *, fps: float = 30, samples_per_segment: int = 24) -> None:
        self.animation, self.fps = animation, fps
        self.samples_per_segment = max(4, samples_per_segment)

    def _sample(self, attribute: str) -> list[tuple[float, float]]:
        samples: list[tuple[float, float]] = []
        for index, (left, right) in enumerate(zip(self.animation.keyframes, self.animation.keyframes[1:])):
            for sample in range(self.samples_per_segment + 1):
                if index and sample == 0:
                    continue
                time = left.time + (right.time - left.time) * sample / self.samples_per_segment
                samples.append((time, evaluate(self.animation, time, attribute)))
        return samples

    @staticmethod
    def _piecewise(samples: list[tuple[float, float]], variable: str) -> str:
        if len(samples) < 2:
            return f"{samples[0][1]:.8f}"
        expression = f"{samples[-1][1]:.8f}"
        for (start, start_value), (end, end_value) in reversed(list(zip(samples, samples[1:]))):
            duration = max(1e-6, end - start)
            linear = f"({start_value:.8f}+({end_value - start_value:.8f})*clip(({variable}-{start:.8f})/{duration:.8f},0,1))"
            expression = f"if(lt({variable},{end:.8f}),{linear},{expression})"
        return expression

    def compile(self) -> FFmpegTransformExpressions:
        time_variable = f"on/{self.fps:.8f}"  # zoompan exposes the sequential output-frame index as `on`.
        zoom = self._piecewise(self._sample("scale"), time_variable)
        camera_z = self._piecewise(self._sample("z"), time_variable)
        position_x = self._piecewise(self._sample("x"), time_variable)
        position_y = self._piecewise(self._sample("y"), time_variable)
        rotation = self._piecewise(self._sample("rotation_degrees"), "t")
        return FFmpegTransformExpressions(
            zoom=zoom,
            camera_z=camera_z,
            x=f"(iw-iw/zoom)*({position_x})",
            y=f"(ih-ih/zoom)*({position_y})",
            rotation_radians=f"({rotation})*PI/180",
            sample_count=len(self._sample("scale")),
        )

    def zoompan_filter(self, *, width: int, height: int) -> str:
        expressions = self.compile()
        return (
            f"zoompan=z='{expressions.zoom}':x='{expressions.x}':y='{expressions.y}':d=1:"
            f"s={width}x{height}:fps={self.fps:.8f},rotate=a='{expressions.rotation_radians}':ow=iw:oh=ih:c=none"
        )


def parallax_zoom_factors(animation: ClipTransformAnimation, time: float, *, foreground_strength: float = 1.0, background_strength: float = .28) -> tuple[float, float]:
    """Map Z-camera movement into separate foreground/background scale factors for 2.5D compositing."""
    z = evaluate(animation, time, "z")
    return 1 + z * foreground_strength, 1 + z * background_strength
