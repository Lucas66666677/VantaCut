export type EasingKind = "linear" | "ease-in-out" | "cubic-bezier";

export interface CubicBezier {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface TransformValue {
  /** Normalised anchor in the source frame; 0.5, 0.5 is centre. */
  x: number;
  y: number;
  scale: number;
  rotation_degrees: number;
  /** Virtual camera Z displacement, consumed by the 2.5D parallax renderer. */
  z: number;
}

export interface TransformKeyframe {
  time: number;
  value: TransformValue;
  easing: EasingKind;
  cubic_bezier?: CubicBezier | null;
}

export interface ClipTransformAnimation {
  clip_id?: string | null;
  keyframes: TransformKeyframe[];
}

export const DEFAULT_BEZIER: CubicBezier = { x1: 0.42, y1: 0, x2: 0.58, y2: 1 };

export function createDefaultAnimation(clipId: string, duration: number): ClipTransformAnimation {
  const base: TransformValue = { x: 0.5, y: 0.5, scale: 1, rotation_degrees: 0, z: 0 };
  return {
    clip_id: clipId,
    keyframes: [
      { time: 0, value: base, easing: "ease-in-out" },
      { time: Math.max(0.1, duration), value: { ...base, scale: 1.12, z: 0.12 }, easing: "ease-in-out" },
    ],
  };
}

function cubic(value0: number, value1: number, value2: number, value3: number, t: number): number {
  const inverse = 1 - t;
  return inverse ** 3 * value0 + 3 * inverse ** 2 * t * value1 + 3 * inverse * t ** 2 * value2 + t ** 3 * value3;
}

/** Inverts cubic x with a bounded Newton solve, then evaluates y: identical math is used by graph and preview. */
export function evaluateCubicBezier(curve: CubicBezier, progress: number): number {
  const target = Math.max(0, Math.min(1, progress)); let t = target;
  for (let iteration = 0; iteration < 7; iteration += 1) {
    const x = cubic(0, curve.x1, curve.x2, 1, t); const derivative = 3 * (1 - t) ** 2 * curve.x1 + 6 * (1 - t) * t * (curve.x2 - curve.x1) + 3 * t ** 2 * (1 - curve.x2);
    if (Math.abs(derivative) < .00001) break;
    t = Math.max(0, Math.min(1, t - (x - target) / derivative));
  }
  return cubic(0, curve.y1, curve.y2, 1, t);
}

/** Interpolates every transform property at a clip-local timestamp. */
export function evaluateTransformAt(animation: ClipTransformAnimation, time: number): TransformValue {
  const frames = [...animation.keyframes].sort((left, right) => left.time - right.time);
  if (!frames.length) return { x: .5, y: .5, scale: 1, rotation_degrees: 0, z: 0 };
  if (time <= frames[0].time) return frames[0].value;
  const last = frames.at(-1)!; if (time >= last.time) return last.value;
  const index = frames.findIndex((frame, frameIndex) => frameIndex < frames.length - 1 && time >= frame.time && time <= frames[frameIndex + 1].time);
  const from = frames[index]; const to = frames[index + 1]; const raw = (time - from.time) / Math.max(.0001, to.time - from.time);
  const progress = from.easing === "cubic-bezier" && from.cubic_bezier ? evaluateCubicBezier(from.cubic_bezier, raw) : from.easing === "ease-in-out" ? raw * raw * (3 - 2 * raw) : raw;
  return {
    x: from.value.x + (to.value.x - from.value.x) * progress, y: from.value.y + (to.value.y - from.value.y) * progress,
    scale: from.value.scale + (to.value.scale - from.value.scale) * progress,
    rotation_degrees: from.value.rotation_degrees + (to.value.rotation_degrees - from.value.rotation_degrees) * progress,
    z: from.value.z + (to.value.z - from.value.z) * progress,
  };
}
