"use client";

import { create } from "zustand";

export type OptimisticEffectKind = "matting" | "inpainting" | "noise_reduction" | "studio_sound" | "filter";
export type OptimisticEffectState = "optimistic" | "processing" | "completed" | "failed";

export interface OptimisticEffectJob {
  id: string;
  kind: OptimisticEffectKind;
  state: OptimisticEffectState;
  taskId?: string;
  clipId?: string;
  mediaAssetId?: string;
  message?: string;
  createdAt: number;
}

interface OptimisticEffectsState {
  jobs: Record<string, OptimisticEffectJob>;
  begin: (job: Omit<OptimisticEffectJob, "id" | "state" | "createdAt">) => string;
  attachTask: (id: string, taskId: string) => void;
  settleTask: (taskId: string, state: Extract<OptimisticEffectState, "processing" | "completed" | "failed">, message?: string | null) => void;
  complete: (id: string, message?: string) => void;
  fail: (id: string, message: string) => void;
  remove: (id: string) => void;
  removeForClipEffect: (clipId: string, kind: OptimisticEffectKind) => void;
}

export const useOptimisticEffectsStore = create<OptimisticEffectsState>((set, get) => ({
  jobs: {},
  begin: (job) => {
    const id = `optimistic-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    set((state) => ({ jobs: { ...state.jobs, [id]: { ...job, id, state: "optimistic", createdAt: Date.now() } } }));
    return id;
  },
  attachTask: (id, taskId) => set((state) => {
    const job = state.jobs[id];
    return job ? { jobs: { ...state.jobs, [id]: { ...job, taskId, state: "processing" } } } : state;
  }),
  settleTask: (taskId, nextState, message) => set((state) => {
    const entry = Object.entries(state.jobs).find(([, job]) => job.taskId === taskId);
    if (!entry) return state;
    const [id, job] = entry;
    return { jobs: { ...state.jobs, [id]: { ...job, state: nextState, message: message ?? job.message } } };
  }),
  complete: (id, message) => set((state) => {
    const job = state.jobs[id];
    return job ? { jobs: { ...state.jobs, [id]: { ...job, state: "completed", message: message ?? job.message } } } : state;
  }),
  fail: (id, message) => set((state) => {
    const job = state.jobs[id];
    return job ? { jobs: { ...state.jobs, [id]: { ...job, state: "failed", message } } } : state;
  }),
  remove: (id) => set((state) => {
    const jobs = { ...state.jobs }; delete jobs[id]; return { jobs };
  }),
  removeForClipEffect: (clipId, kind) => set((state) => ({
    jobs: Object.fromEntries(Object.entries(state.jobs).filter(([, job]) => job.clipId !== clipId || job.kind !== kind)),
  })),
}));

export function optimisticJobsForClip(jobs: Record<string, OptimisticEffectJob>, clipId: string, sourceAssetId?: string): OptimisticEffectJob[] {
  return Object.values(jobs).filter((job) => job.clipId === clipId || (sourceAssetId !== undefined && job.mediaAssetId === sourceAssetId));
}
