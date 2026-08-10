export type WorkspaceModuleId = "timeline" | "inspector" | "color_wheels" | "scopes" | "audio_mixer";
export type WorkspaceMode = "welcome" | "editing" | "color" | "audio";
export type WorkspaceRegion = "center" | "right" | "bottom";

export interface WorkspaceModuleLayout {
  enabled: boolean;
  collapsed: boolean;
  region: WorkspaceRegion;
  order: number;
}

export interface WorkspaceLayoutDocument {
  version: number;
  mode: WorkspaceMode;
  modules: Record<WorkspaceModuleId, WorkspaceModuleLayout>;
}

export interface WorkspaceIntent {
  mode: WorkspaceMode;
  modules: WorkspaceModuleId[];
  summary: string;
}
