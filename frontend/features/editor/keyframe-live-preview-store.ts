"use client";

import { create } from "zustand";

import type { TransformValue } from "@/types/keyframes";

interface KeyframeLivePreviewState {
  transform: { clipId: string; value: TransformValue } | null;
  setTransform: (clipId: string, value: TransformValue) => void;
  clear: (clipId?: string) => void;
}

/** Hot-path bridge from graph handles to the Worker-backed preview; deliberately excluded from undo/persistence. */
export const useKeyframeLivePreviewStore = create<KeyframeLivePreviewState>((set, get) => ({
  transform: null,
  setTransform: (clipId, value) => set({ transform: { clipId, value } }),
  clear: (clipId) => { if (!clipId || get().transform?.clipId === clipId) set({ transform: null }); },
}));
