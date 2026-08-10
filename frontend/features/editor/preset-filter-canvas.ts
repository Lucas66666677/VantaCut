export type PresetFilterId =
  | "vintage_film" | "nordic_cinematic" | "vibrant_vlog" | "golden_hour" | "moody_teal"
  | "soft_portrait" | "noir_contrast" | "pastel_dream" | "urban_night" | "clean_luxury";

export interface ColorFilterPreset { id: PresetFilterId; name: string; description: string; accent: string; }
interface Profile { contrast: number; saturation: number; gamma: number; lift: number; gains: [number, number, number]; }

export const COLOR_FILTER_PRESETS: ColorFilterPreset[] = [
  { id: "vintage_film", name: "Vintage Film", description: "暖褐黑位與柔和底片感", accent: "#b76d3b" },
  { id: "nordic_cinematic", name: "Nordic Cinematic", description: "冷冽低飽和的北歐電影調", accent: "#6f96a8" },
  { id: "vibrant_vlog", name: "Vibrant Vlog", description: "明亮飽和的日常 Vlog", accent: "#ff7658" },
  { id: "golden_hour", name: "Golden Hour", description: "金色夕陽與柔暖高光", accent: "#eead4f" },
  { id: "moody_teal", name: "Moody Teal", description: "青橙對比、濃郁戲劇感", accent: "#237e88" },
  { id: "soft_portrait", name: "Soft Portrait", description: "柔膚、低反差的人像質感", accent: "#e8a9a7" },
  { id: "noir_contrast", name: "Noir Contrast", description: "強烈黑白、都會敘事感", accent: "#808080" },
  { id: "pastel_dream", name: "Pastel Dream", description: "明亮奶油色與夢幻粉調", accent: "#d891c5" },
  { id: "urban_night", name: "Urban Night", description: "深藍夜景與霓虹氛圍", accent: "#354a9c" },
  { id: "clean_luxury", name: "Clean Luxury", description: "乾淨中性、精緻商業感", accent: "#d5c6aa" },
];

const profiles: Record<PresetFilterId, Profile> = {
  vintage_film: { contrast: .92, saturation: .78, gamma: 1.08, lift: .035, gains: [1.10, 1, .84] }, nordic_cinematic: { contrast: 1.10, saturation: .73, gamma: 1.03, lift: .012, gains: [.86, 1, 1.13] }, vibrant_vlog: { contrast: 1.06, saturation: 1.26, gamma: 1.04, lift: .005, gains: [1.06, 1.02, .98] }, golden_hour: { contrast: .98, saturation: 1.10, gamma: 1.10, lift: .018, gains: [1.16, 1.05, .82] }, moody_teal: { contrast: 1.18, saturation: .92, gamma: .96, lift: 0, gains: [1.07, 1.01, 1.14] }, soft_portrait: { contrast: .86, saturation: .88, gamma: 1.10, lift: .040, gains: [1.08, 1.01, .96] }, noir_contrast: { contrast: 1.42, saturation: .03, gamma: .96, lift: 0, gains: [1, 1, 1] }, pastel_dream: { contrast: .84, saturation: .74, gamma: 1.15, lift: .052, gains: [1.10, 1.02, 1.09] }, urban_night: { contrast: 1.22, saturation: 1.13, gamma: .91, lift: 0, gains: [.90, .96, 1.22] }, clean_luxury: { contrast: 1.04, saturation: .88, gamma: 1.03, lift: .008, gains: [1.02, 1.01, .98] },
};
const clamp = (value: number) => Math.min(1, Math.max(0, value));

/** Small Canvas preview renderer. Keep its profile coefficients aligned with preset_luts.py. */
export function drawPresetFilter(canvas: HTMLCanvasElement, source: CanvasImageSource, preset: PresetFilterId, intensity: number) {
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context || canvas.width === 0 || canvas.height === 0) return;
  context.drawImage(source, 0, 0, canvas.width, canvas.height);
  const image = context.getImageData(0, 0, canvas.width, canvas.height); const weight = clamp(intensity / 100); const profile = profiles[preset];
  for (let index = 0; index < image.data.length; index += 4) {
    const original = [image.data[index] / 255, image.data[index + 1] / 255, image.data[index + 2] / 255];
    let [red, green, blue] = original.map((value, channel) => clamp(value * profile.gains[channel])) as [number, number, number];
    const luma = red * .2126 + green * .7152 + blue * .0722;
    red = (luma + (red - luma) * profile.saturation - .5) * profile.contrast + .5 + profile.lift;
    green = (luma + (green - luma) * profile.saturation - .5) * profile.contrast + .5 + profile.lift;
    blue = (luma + (blue - luma) * profile.saturation - .5) * profile.contrast + .5 + profile.lift;
    const graded = [red, green, blue].map((value) => clamp(value) ** (1 / profile.gamma));
    image.data[index] = Math.round((original[0] * (1 - weight) + graded[0] * weight) * 255);
    image.data[index + 1] = Math.round((original[1] * (1 - weight) + graded[1] * weight) * 255);
    image.data[index + 2] = Math.round((original[2] * (1 - weight) + graded[2] * weight) * 255);
  }
  context.putImageData(image, 0, 0);
}

