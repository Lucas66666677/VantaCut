import { create } from "zustand";

import type { WorkspaceIntent, WorkspaceLayoutDocument, WorkspaceModuleId, WorkspaceMode } from "@/types/workspace";

const defaultLayout = (): WorkspaceLayoutDocument => ({
  version: 1,
  mode: "welcome",
  modules: {
    timeline: { enabled: false, collapsed: false, region: "center", order: 0 },
    inspector: { enabled: false, collapsed: false, region: "right", order: 0 },
    color_wheels: { enabled: false, collapsed: false, region: "right", order: 1 },
    scopes: { enabled: false, collapsed: false, region: "bottom", order: 0 },
    audio_mixer: { enabled: false, collapsed: false, region: "bottom", order: 1 },
  },
});

interface WorkspaceState extends WorkspaceLayoutDocument {
  hydrated: boolean;
  dirty: boolean;
  applyIntent: (intent: WorkspaceIntent) => void;
  toggleModule: (moduleId: WorkspaceModuleId) => void;
  setCollapsed: (moduleId: WorkspaceModuleId, collapsed: boolean) => void;
  hydrate: (layout: WorkspaceLayoutDocument) => void;
  markPersisted: () => void;
  reset: () => void;
  snapshot: () => WorkspaceLayoutDocument;
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  ...defaultLayout(),
  hydrated: false,
  dirty: false,

  applyIntent: (intent) => set((state) => ({
    mode: intent.mode,
    modules: Object.fromEntries(Object.entries(state.modules).map(([id, module]) => [
      id,
      { ...module, enabled: intent.modules.includes(id as WorkspaceModuleId), collapsed: false },
    ])) as WorkspaceLayoutDocument["modules"],
    dirty: true,
  })),
  toggleModule: (moduleId) => set((state) => ({
    modules: { ...state.modules, [moduleId]: { ...state.modules[moduleId], enabled: !state.modules[moduleId].enabled } },
    dirty: true,
  })),
  setCollapsed: (moduleId, collapsed) => set((state) => ({
    modules: { ...state.modules, [moduleId]: { ...state.modules[moduleId], collapsed } },
    dirty: true,
  })),
  hydrate: (layout) => set({ ...layout, hydrated: true, dirty: false }),
  markPersisted: () => set({ dirty: false }),
  reset: () => set({ ...defaultLayout(), hydrated: true, dirty: true }),
  snapshot: () => {
    const { version, mode, modules } = get();
    return structuredClone({ version, mode, modules });
  },
}));

export const workspaceModuleLabels: Record<WorkspaceModuleId, string> = {
  timeline: "時間軸",
  inspector: "Inspector",
  color_wheels: "Color Wheels",
  scopes: "Scopes",
  audio_mixer: "Audio Mixer",
};
