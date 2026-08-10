"use client";

import { useEditorPerformanceStore } from "@/features/performance/editor-performance-store";

export function EditorSafetyStatus({ recoveryStatus }: { recoveryStatus: "idle" | "restoring" | "recovered" | "ready" | "error" }) {
  const { quality, heapRatio, backgroundPreRenderEnabled } = useEditorPerformanceStore();
  const text = quality === "emergency" ? "記憶體壓力：已降為 360p，背景預渲染暫停" : quality === "reduced" ? "記憶體壓力：已降低預覽品質" : "效能保護正常";
  const restore = recoveryStatus === "recovered" ? "已從本機崩潰復原" : recoveryStatus === "restoring" ? "正在讀取本機復原點…" : recoveryStatus === "error" ? "本機復原暫時不可用" : "本機即時復原已啟用";
  return <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-zinc-500"><span className={quality === "full" ? "text-emerald-400" : "text-amber-300"}>{text}{heapRatio !== null ? `（Heap ${Math.round(heapRatio * 100)}%）` : ""}</span><span>·</span><span>{restore}</span>{!backgroundPreRenderEnabled && <span className="text-amber-300">· Proxy 預讀已暫停</span>}</div>;
}
