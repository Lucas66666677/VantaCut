import { create } from "zustand";

export type ProjectJobStatus = "idle" | "processing" | "completed" | "failed";

export interface ProjectStatusEvent {
  project_id: string;
  progress: number;
  stage: string;
  status: ProjectJobStatus;
  message?: string | null;
  job_id?: string | null;
  updated_at?: string;
  connected?: boolean;
}

interface ProjectStatusState {
  projects: Record<string, ProjectStatusEvent>;
  setProjectStatus: (status: ProjectStatusEvent) => void;
  setConnectionState: (projectId: string, connected: boolean) => void;
}

export const useProjectStatusStore = create<ProjectStatusState>((set) => ({
  projects: {},
  setProjectStatus: (status) => set((state) => ({
    projects: {
      ...state.projects,
      [status.project_id]: { ...state.projects[status.project_id], ...status },
    },
  })),
  setConnectionState: (projectId, connected) => set((state) => {
    const current = state.projects[projectId];
    return {
      projects: {
        ...state.projects,
        [projectId]: {
          ...current,
          project_id: projectId,
          progress: current?.progress ?? 0,
          stage: current?.stage ?? "idle",
          status: current?.status ?? "idle",
          connected,
        },
      },
    };
  }),
}));
