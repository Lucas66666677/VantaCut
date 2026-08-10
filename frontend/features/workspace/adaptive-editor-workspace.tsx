"use client";

import { FormEvent, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

import { classifyWorkspaceIntent } from "@/features/workspace/workspace-intent";
import { useWorkspacePersistence } from "@/features/workspace/use-workspace-persistence";
import { useWorkspaceStore, workspaceModuleLabels } from "@/features/workspace/workspace-store";
import { DockableWorkspace } from "@/features/workspace/dockable-workspace";
import { LocalMediaBin } from "@/features/media/local-media-bin";
import { MobilePreviewHandoff } from "@/features/editor/mobile-preview-handoff";
import { useCloudTimelineDraft } from "@/features/editor/use-cloud-timeline-draft";
import { EditorSafetyStatus } from "@/features/performance/editor-safety-status";
import { useCrashRecovery } from "@/features/performance/use-crash-recovery";
import { useMemoryPressure } from "@/features/performance/use-memory-pressure";
import { OfflineModeToast } from "@/features/editor/resilience-feedback";
import { AvLatencyCalibrationSettings, AvLatencyIndicator } from "@/features/editor/av-latency-calibration";
import { SemanticMediaBin } from "@/features/media/semantic-media-bin";
import type { TimelineClipInput } from "@/types/timeline";
import type { WorkspaceModuleId } from "@/types/workspace";

interface AdaptiveEditorWorkspaceProps {
  timeline: TimelineClipInput[];
  timelineId?: string;
  projectId?: string;
  userId?: string;
}

export function AdaptiveEditorWorkspace({ timeline, timelineId, projectId, userId }: AdaptiveEditorWorkspaceProps) {
  const mode = useWorkspaceStore((state) => state.mode);
  const modules = useWorkspaceStore((state) => state.modules);
  const applyIntent = useWorkspaceStore((state) => state.applyIntent);
  const toggleModule = useWorkspaceStore((state) => state.toggleModule);
  const reset = useWorkspaceStore((state) => state.reset);
  const { error } = useWorkspacePersistence(projectId, userId);
  useMemoryPressure();
  const recovery = useCrashRecovery(timelineId);
  // A fresh local recovery point is newer than a 30-second cloud checkpoint.
  const cloudDraft = useCloudTimelineDraft(timelineId, userId, recovery.status === "ready" || recovery.status === "error");
  useEffect(() => {
    const save = () => { void cloudDraft.saveNow(); };
    window.addEventListener("editor-project-save", save);
    return () => window.removeEventListener("editor-project-save", save);
  }, [cloudDraft.saveNow]);
  const [prompt, setPrompt] = useState("");
  const [assistantMessage, setAssistantMessage] = useState("我可以依你的工作意圖展開需要的工具。");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const intent = classifyWorkspaceIntent(prompt);
    applyIntent(intent);
    setAssistantMessage(intent.summary);
    setPrompt("");
  };
  const activeModules = (Object.keys(modules) as WorkspaceModuleId[]).filter((id) => modules[id].enabled);

  return <main className="mx-auto min-h-screen max-w-7xl p-6 md:p-10">
    <OfflineModeToast active={cloudDraft.status === "error"} />
    <header className="mb-6 flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-medium uppercase tracking-[.2em] text-cyan-300">Adaptive Workspace</p><h1 className="mt-1 text-2xl font-semibold">{mode === "welcome" ? "從一句話開始剪輯" : "你的專屬剪輯工作區"}</h1>{timelineId && userId && <p className="mt-1 text-xs text-zinc-500">雲端草稿：{cloudDraft.status === "saved" ? "已同步" : cloudDraft.status === "loading" ? "載入中" : cloudDraft.status === "error" ? "同步失敗" : "尚未建立"}</p>}<EditorSafetyStatus recoveryStatus={recovery.status} /></div><div className="flex items-center gap-2"><AvLatencyIndicator /><AvLatencyCalibrationSettings />{timelineId && userId && <MobilePreviewHandoff timelineId={timelineId} userId={userId} />}{mode !== "welcome" && <button onClick={reset} className="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300">回到簡潔模式</button>}</div></header>
    <section className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4 shadow-xl"><form onSubmit={submit} className="flex gap-2"><input value={prompt} onChange={(event) => setPrompt(event.target.value)} className="min-w-0 flex-1 rounded-xl border border-zinc-700 bg-zinc-950 px-4 py-3 text-sm outline-none focus:border-cyan-400" placeholder="例如：幫我精細調色，或調整人聲混音" /><button className="rounded-xl bg-cyan-400 px-4 py-2 text-sm font-semibold text-zinc-950">交給 AI</button></form><p className="mt-2 text-xs text-zinc-400">{assistantMessage}</p>{error && <p className="mt-1 text-xs text-amber-300">{error}；目前以本機工作區繼續。</p>}</section>
    {mode === "welcome" ? <div className="mt-5 space-y-3"><LocalMediaBin projectId={projectId} /><SemanticMediaBin projectId={projectId} /><p className="text-center text-sm text-zinc-500">拖入素材後可立刻開始剪輯；雲端同步會在背景安靜完成。</p></div> : <>
      <nav className="mt-5 flex flex-wrap gap-2">{(Object.keys(modules) as WorkspaceModuleId[]).map((moduleId) => <button key={moduleId} onClick={() => toggleModule(moduleId)} className={`rounded-full border px-3 py-1.5 text-xs ${modules[moduleId].enabled ? "border-cyan-400/60 bg-cyan-400/10 text-cyan-100" : "border-zinc-700 text-zinc-500"}`}>{workspaceModuleLabels[moduleId]}</button>)}</nav>
      <div className="mt-5"><SemanticMediaBin projectId={projectId} /></div>
      <DockableWorkspace mode={mode} enabledPanels={activeModules} timeline={timeline} timelineId={timelineId} projectId={projectId} userId={userId} />
    </>}
  </main>;
}
