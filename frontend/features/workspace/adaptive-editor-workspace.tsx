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
import { useAuthStore } from "@/lib/auth/auth-store";
import type { TimelineClipInput } from "@/types/timeline";
import type { WorkspaceModuleId } from "@/types/workspace";

interface AdaptiveEditorWorkspaceProps {
  timeline: TimelineClipInput[];
  timelineId?: string;
  projectId?: string;
}

export function AdaptiveEditorWorkspace({ timeline, timelineId, projectId }: AdaptiveEditorWorkspaceProps) {
  const userId = useAuthStore((state) => state.user?.id);
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

  return <main className="min-h-screen bg-[var(--lr-color-background)] text-[var(--lr-color-text-primary)]">
    <OfflineModeToast active={cloudDraft.status === "error"} />
    <header className="border-b border-[var(--lr-color-border)] bg-[var(--lr-color-surface)]">
      <div className="mx-auto flex min-h-16 max-w-[96rem] flex-wrap items-center justify-between gap-3 px-4 py-3 md:px-6">
        <div><p className="text-[11px] font-semibold uppercase tracking-[.18em] text-[var(--lr-color-secondary)]">VantaCut / Workspace</p><div className="mt-1 flex flex-wrap items-center gap-3"><h1 className="text-lg font-semibold">{mode === "welcome" ? "開始新的剪輯" : "專案工作區"}</h1>{timelineId && userId && <span className="border-l border-[var(--lr-color-border)] pl-3 text-xs text-[var(--lr-color-text-muted)]">雲端草稿：{cloudDraft.status === "saved" ? "已同步" : cloudDraft.status === "loading" ? "載入中" : cloudDraft.status === "error" ? "同步失敗" : "尚未建立"}</span>}</div><EditorSafetyStatus recoveryStatus={recovery.status} /></div>
        <div className="flex items-center gap-2"><AvLatencyIndicator /><AvLatencyCalibrationSettings />{timelineId && userId && <MobilePreviewHandoff timelineId={timelineId} />}{mode !== "welcome" && <button onClick={reset} className="rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] bg-[var(--lr-color-surface-raised)] px-3 py-2 text-xs text-[var(--lr-color-text-secondary)] hover:border-[var(--lr-color-border-strong)] hover:text-[var(--lr-color-text-primary)]">精簡介面</button>}</div>
      </div>
    </header>
    <div className="mx-auto max-w-[96rem] px-4 py-4 md:px-6">
      <section className="border border-[var(--lr-color-border)] bg-[var(--lr-color-surface)] p-3 shadow-[var(--lr-shadow-sm)]"><form onSubmit={submit} className="flex gap-2"><label htmlFor="workspace-intent" className="sr-only">描述剪輯需求</label><input id="workspace-intent" value={prompt} onChange={(event) => setPrompt(event.target.value)} className="min-w-0 flex-1 rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] bg-[var(--lr-color-background)] px-3 py-2.5 text-sm outline-none hover:border-[var(--lr-color-border-strong)] focus:border-[var(--lr-color-primary)]" placeholder="描述工作意圖，例如：精細調色或調整人聲混音" /><button className="rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary)] px-4 py-2 text-sm font-semibold text-[var(--lr-color-text-inverse)] hover:bg-[var(--lr-color-primary-strong)]">套用工作區</button></form><p className="mt-2 text-xs text-[var(--lr-color-text-muted)]">{assistantMessage}</p>{error && <p className="mt-1 text-xs text-[var(--lr-color-warning)]">{error}；目前以本機工作區繼續。</p>}</section>
      {mode === "welcome" ? <div className="mt-4 space-y-3"><LocalMediaBin projectId={projectId} /><SemanticMediaBin projectId={projectId} /><p className="text-center text-sm text-[var(--lr-color-text-muted)]">拖入素材後可立即開始；雲端同步會在背景完成。</p></div> : <>
        <nav aria-label="工作區模組" className="mt-4 flex flex-wrap gap-1 border-b border-[var(--lr-color-border)]">{(Object.keys(modules) as WorkspaceModuleId[]).map((moduleId) => <button key={moduleId} onClick={() => toggleModule(moduleId)} className={`border-b-2 px-3 py-2 text-xs font-medium ${modules[moduleId].enabled ? "border-[var(--lr-color-primary)] text-[var(--lr-color-primary-strong)]" : "border-transparent text-[var(--lr-color-text-muted)] hover:text-[var(--lr-color-text-secondary)]"}`}>{workspaceModuleLabels[moduleId]}</button>)}</nav>
        <div className="mt-4"><SemanticMediaBin projectId={projectId} /></div>
        <DockableWorkspace mode={mode} enabledPanels={activeModules} timeline={timeline} timelineId={timelineId} projectId={projectId} userId={userId} />
      </>}
    </div>
  </main>;
}
