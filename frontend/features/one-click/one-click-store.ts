import { create } from "zustand";

export interface OneClickTemplate {
  id: string;
  name: string;
  aspect_ratio: "16:9" | "9:16";
  bgm: { search_keywords?: string[]; target_bpm?: number; mix_level?: number };
  slot_count: number;
  total_beats: number;
}

interface OneClickState {
  templates: OneClickTemplate[];
  selectedTemplateId: string | null;
  taskId: string | null;
  isGenerating: boolean;
  error: string | null;
  setTemplates: (templates: OneClickTemplate[]) => void;
  selectTemplate: (templateId: string) => void;
  start: () => void;
  finish: () => void;
  fail: (message: string) => void;
}

export const useOneClickStore = create<OneClickState>((set) => ({
  templates: [], selectedTemplateId: null, taskId: null, isGenerating: false, error: null,
  setTemplates: (templates) => set((state) => ({ templates, selectedTemplateId: state.selectedTemplateId ?? templates[0]?.id ?? null })),
  selectTemplate: (selectedTemplateId) => set({ selectedTemplateId, error: null }),
  start: () => set({ isGenerating: true, error: null, taskId: null }),
  finish: () => set({ isGenerating: false }),
  fail: (error) => set({ isGenerating: false, error }),
}));
