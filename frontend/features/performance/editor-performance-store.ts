import { create } from "zustand";

export type PreviewQuality = "full" | "reduced" | "emergency";

interface EditorPerformanceState {
  quality: PreviewQuality;
  heapRatio: number | null;
  backgroundPreRenderEnabled: boolean;
  antiAliasingEnabled: boolean;
  previewMaxHeight: number;
  setMemoryPressure: (heapRatio: number | null) => void;
}

/** One source of truth shared by workers, preview canvases, and idle pre-render jobs. */
export const useEditorPerformanceStore = create<EditorPerformanceState>((set) => ({
  quality: "full",
  heapRatio: null,
  backgroundPreRenderEnabled: true,
  antiAliasingEnabled: true,
  previewMaxHeight: 720,
  setMemoryPressure: (heapRatio) => {
    if (heapRatio !== null && heapRatio >= 0.85) {
      set({ quality: "emergency", heapRatio, backgroundPreRenderEnabled: false, antiAliasingEnabled: false, previewMaxHeight: 360 });
    } else if (heapRatio !== null && heapRatio >= 0.72) {
      set({ quality: "reduced", heapRatio, backgroundPreRenderEnabled: false, antiAliasingEnabled: false, previewMaxHeight: 540 });
    } else if (heapRatio === null || heapRatio < 0.65) {
      // Hysteresis avoids resolution oscillating around the pressure boundary.
      set({ quality: "full", heapRatio, backgroundPreRenderEnabled: true, antiAliasingEnabled: true, previewMaxHeight: 720 });
    } else {
      set({ heapRatio });
    }
  },
}));
