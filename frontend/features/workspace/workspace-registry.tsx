"use client";

import dynamic from "next/dynamic";
import type { ComponentType } from "react";

import type { WorkspaceModuleId } from "@/types/workspace";

const loading = () => <div className="rounded-2xl border border-zinc-800 bg-zinc-900 p-5 text-sm text-zinc-500">正在載入專業工具…</div>;

// These imports form independent client chunks; inactive professional tools do
// not inflate the beginner workspace's initial JavaScript payload.
export const workspaceModuleRegistry: Record<WorkspaceModuleId, ComponentType<any>> = {
  timeline: dynamic(() => import("@/features/workspace/modules/timeline-module").then((module) => module.TimelineWorkspaceModule), { ssr: false, loading }),
  inspector: dynamic(() => import("@/features/workspace/modules/inspector-module").then((module) => module.InspectorWorkspaceModule), { ssr: false, loading }),
  color_wheels: dynamic(() => import("@/features/workspace/modules/color-wheels-module").then((module) => module.ColorWheelsWorkspaceModule), { ssr: false, loading }),
  scopes: dynamic(() => import("@/features/workspace/modules/scopes-module").then((module) => module.ScopesWorkspaceModule), { ssr: false, loading }),
  audio_mixer: dynamic(() => import("@/features/workspace/modules/audio-mixer-module").then((module) => module.AudioMixerWorkspaceModule), { ssr: false, loading }),
};
