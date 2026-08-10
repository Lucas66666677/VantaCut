export type SpeedCurvePreset = "hero" | "flash_in" | "montage" | "custom";
export interface SpeedCurvePoint { position: number; speed: number; }
export interface ClipSpeedCurve { clip_id: string; preset: SpeedCurvePreset; points: SpeedCurvePoint[]; }

export const SPEED_CURVE_PRESETS: Record<Exclude<SpeedCurvePreset, "custom">, Omit<ClipSpeedCurve, "clip_id">> = {
  hero: { preset: "hero", points: [{ position: 0, speed: 1 }, { position: .22, speed: 2.4 }, { position: .56, speed: .25 }, { position: .80, speed: 1.65 }, { position: 1, speed: 1 }] },
  flash_in: { preset: "flash_in", points: [{ position: 0, speed: 4.5 }, { position: .18, speed: 2.2 }, { position: .48, speed: 1 }, { position: 1, speed: 1 }] },
  montage: { preset: "montage", points: [{ position: 0, speed: 1 }, { position: .22, speed: 2.7 }, { position: .45, speed: .7 }, { position: .70, speed: 2.2 }, { position: 1, speed: 1.15 }] },
};

export function speedToGraphY(speed: number): number {
  return 1 - (Math.log10(Math.max(.1, Math.min(10, speed))) + 1) / 2;
}

export function graphYToSpeed(y: number): number {
  return Math.max(.1, Math.min(10, 10 ** (1 - 2 * Math.max(0, Math.min(1, y)))));
}

