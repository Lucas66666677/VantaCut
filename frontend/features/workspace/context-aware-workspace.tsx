"use client";

import { useEffect, useMemo, useState } from "react";

import type { TimelineClip } from "@/types/timeline";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export type WorkspaceMode = "steam" | "landscape" | "person" | "general";
export type WorkspaceTool = "ar_arrows" | "code_highlight" | "screen_focus" | "color_match" | "cinematic_transition" | "auto_b_roll" | "portrait_matting" | "beauty" | "auto_reframe" | "filter" | "captions" | "transition";

export interface WorkspaceContext { mode: WorkspaceMode; confidence: number; reasons: string[]; priority_tools: WorkspaceTool[]; }

const TOOL_LABELS: Record<WorkspaceTool, { title: string; detail: string; accent: string }> = {
  ar_arrows: { title: "AR 箭頭", detail: "標示接線與元件方向", accent: "border-orange-300/50 bg-orange-400/10 text-orange-100" },
  code_highlight: { title: "程式碼高亮", detail: "對應動作與程式行", accent: "border-orange-300/50 bg-orange-400/10 text-orange-100" },
  screen_focus: { title: "教學聚焦", detail: "游標、視窗與局部放大", accent: "border-orange-300/50 bg-orange-400/10 text-orange-100" },
  color_match: { title: "色彩匹配", detail: "套用參考圖的電影感", accent: "border-cyan-300/50 bg-cyan-400/10 text-cyan-100" },
  cinematic_transition: { title: "電影感轉場", detail: "讓風景鏡頭自然銜接", accent: "border-cyan-300/50 bg-cyan-400/10 text-cyan-100" },
  auto_b_roll: { title: "風景 B-Roll", detail: "補足敘事畫面", accent: "border-cyan-300/50 bg-cyan-400/10 text-cyan-100" },
  portrait_matting: { title: "一鍵去背", detail: "人物與背景分離", accent: "border-emerald-300/50 bg-emerald-400/10 text-emerald-100" },
  beauty: { title: "臉部美顏", detail: "自然磨皮與提亮", accent: "border-emerald-300/50 bg-emerald-400/10 text-emerald-100" },
  auto_reframe: { title: "追蹤主角", detail: "自動直式跟拍", accent: "border-emerald-300/50 bg-emerald-400/10 text-emerald-100" },
  filter: { title: "質感濾鏡", detail: "先用一個簡單滑桿", accent: "border-zinc-600 bg-zinc-800 text-zinc-100" },
  captions: { title: "動態字幕", detail: "讓重點更容易被看見", accent: "border-zinc-600 bg-zinc-800 text-zinc-100" },
  transition: { title: "轉場", detail: "讓片段銜接自然", accent: "border-zinc-600 bg-zinc-800 text-zinc-100" },
};

const FALLBACK: Record<WorkspaceMode, WorkspaceContext> = {
  steam: { mode: "steam", confidence: .62, reasons: ["片段文字包含程式、電路或元件線索"], priority_tools: ["ar_arrows", "code_highlight", "screen_focus"] },
  landscape: { mode: "landscape", confidence: .6, reasons: ["片段文字包含旅行或風景線索"], priority_tools: ["color_match", "cinematic_transition", "auto_b_roll"] },
  person: { mode: "person", confidence: .55, reasons: ["目前以人物主體為主"], priority_tools: ["portrait_matting", "beauty", "auto_reframe"] },
  general: { mode: "general", confidence: .3, reasons: ["使用通用剪輯工作區"], priority_tools: ["filter", "captions", "transition"] },
};

export function inferWorkspaceContext(clip: TimelineClip | null): WorkspaceContext {
  if (!clip) return FALLBACK.general;
  const text = [clip.reason, clip.kind, ...(clip.semantic_tags ?? []), ...(clip.analysis_types ?? []), ...(clip.review_flags ?? []), ...(clip.creator_hints ?? [])].join(" ").toLowerCase();
  if (/(code|circuit|arduino|ide|screen.?record|tinkercad|程式|電路|接線|元件|機器人)/.test(text)) return FALLBACK.steam;
  if (/(landscape|travel|nature|mountain|ocean|forest|sunset|風景|旅行|山景|海景|雪景)/.test(text)) return FALLBACK.landscape;
  if (clip.speaker_state || /(talking|speaker|portrait|人物|講者)/.test(text)) return FALLBACK.person;
  return FALLBACK.general;
}

export function useWorkspaceContext(clip: TimelineClip | null, timelineId?: string, userId?: string): WorkspaceContext {
  const fallback = useMemo(() => inferWorkspaceContext(clip), [clip]);
  const [context, setContext] = useState<WorkspaceContext>(fallback);
  useEffect(() => { setContext(fallback); }, [fallback]);
  useEffect(() => {
    if (!timelineId || !userId || !clip || clip.id.startsWith("ai-clip-")) return;
    const controller = new AbortController();
    void authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/clips/${clip.id}/workspace-context`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("workspace context unavailable")))
      .then((payload: WorkspaceContext) => setContext(payload))
      .catch((error: unknown) => { if ((error as { name?: string }).name !== "AbortError") setContext(fallback); });
    return () => controller.abort();
  }, [clip, fallback, timelineId, userId]);
  return context;
}

export function ContextToolShelf({ context, onTool }: { context: WorkspaceContext; onTool?: (tool: WorkspaceTool) => void }) {
  const title = { steam: "STEAM 教學工具", landscape: "風景敘事工具", person: "人物優化工具", general: "建議工具" }[context.mode];
  return <section className="mt-3 rounded-xl border border-zinc-700/80 bg-zinc-950/70 p-3"><div className="flex items-center justify-between gap-2"><div><h4 className="text-xs font-semibold text-zinc-100">{title}</h4><p className="mt-0.5 text-[10px] text-zinc-500">AI 依素材理解置頂，避免一次塞滿所有工具。</p></div><span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">{Math.round(context.confidence * 100)}%</span></div><div className="mt-2 grid gap-1.5">{context.priority_tools.slice(0, 3).map((tool) => { const item = TOOL_LABELS[tool]; return <button key={tool} type="button" onClick={() => onTool?.(tool)} className={`rounded-lg border px-2.5 py-2 text-left transition hover:brightness-125 ${item.accent}`}><span className="block text-xs font-semibold">{item.title}</span><span className="block text-[10px] opacity-75">{item.detail}</span></button>; })}</div></section>;
}
