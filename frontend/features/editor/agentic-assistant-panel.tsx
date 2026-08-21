"use client";

import { useMemo, useState } from "react";

import { applyAgentToolCalls, describeAgentPlan, serialiseTimelineForAgent, type AgentToolCall } from "@/features/editor/agentic-editing";
import { useAgentProposalStore } from "@/features/editor/agentic-proposal-store";
import type { SandboxSnapshot } from "@/features/editor/non-destructive-history";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

interface AgentPreviewResponse { provider_name: string; tool_calls: AgentToolCall[]; explanation?: string | null; detail?: string; }

export function AgenticAssistantPanel({
  timelineId, snapshot, selectedClipId, onAccept,
}: { timelineId?: string; userId?: string; snapshot: SandboxSnapshot; selectedClipId: string | null; onAccept: (proposal: SandboxSnapshot) => void }) {
  const [instruction, setInstruction] = useState("把前面三段調成賽博龐克風，並且節奏剪緊湊一點");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showComparison, setShowComparison] = useState(false);
  const proposal = useAgentProposalStore((state) => state.proposal);
  const setProposal = useAgentProposalStore((state) => state.setProposal);
  const clearProposal = useAgentProposalStore((state) => state.clearProposal);
  const compactContext = useMemo(() => serialiseTimelineForAgent(snapshot.clips, selectedClipId), [selectedClipId, snapshot.clips]);

  const askAgent = async () => {
    if (!timelineId || !instruction.trim()) return;
    setPending(true); setError(null);
    try {
      const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/agent-preview`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction, timeline_context: compactContext }),
      });
      const result = await response.json() as AgentPreviewResponse;
      if (!response.ok) throw new Error(result.detail ?? "AI 副導演暫時無法規劃修改");
      const calls = result.tool_calls ?? [];
      if (!calls.length) throw new Error(result.explanation ?? "AI 沒有找到可以安全套用的修改；請選取片段或說得更具體。 ");
      setProposal({ instruction, toolCalls: calls, snapshot: applyAgentToolCalls(snapshot, calls), explanation: result.explanation, createdAt: Date.now() });
    } catch (cause) { setError(cause instanceof Error ? cause.message : "無法建立 AI 提案"); } finally { setPending(false); }
  };
  const accept = () => { if (!proposal) return; onAccept(proposal.snapshot); clearProposal(); setShowComparison(false); };

  return <aside className="rounded-xl border border-fuchsia-300/25 bg-gradient-to-b from-fuchsia-950/25 to-zinc-950 p-4">
    <div className="flex items-center justify-between gap-2"><div><h2 className="text-sm font-semibold text-fuchsia-50">AI 副導演</h2><p className="mt-0.5 text-[11px] text-zinc-400">只提出受限 Tool Calls；不會直接覆寫你的時間軸。</p></div><span className="rounded-full border border-fuchsia-300/25 px-2 py-0.5 text-[10px] text-fuchsia-200">Ghost mode</span></div>
    <textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={3} className="mt-3 w-full resize-none rounded-lg border border-zinc-700 bg-zinc-900/90 p-2 text-xs text-zinc-100 outline-none focus:border-fuchsia-300" aria-label="給 AI 副導演的指令" />
    <button type="button" disabled={!timelineId || pending || !instruction.trim()} onClick={() => void askAgent()} className="mt-2 w-full rounded-lg bg-fuchsia-200 px-3 py-2 text-xs font-bold text-zinc-950 disabled:opacity-40">{pending ? "正在讀取時間軸…" : "產生安全提案"}</button>
    {error && <p role="alert" className="mt-2 text-xs text-rose-300">{error}</p>}
    {proposal && <div className="mt-3 rounded-lg border border-dashed border-fuchsia-300/60 bg-fuchsia-500/10 p-3"><p className="text-xs font-medium text-fuchsia-100">提案：{describeAgentPlan(proposal.toolCalls)}</p><p className="mt-1 text-[11px] text-zinc-400">{proposal.toolCalls.length} 個操作已投影到虛線 Ghost Track，尚未寫入歷史紀錄。</p><div className="mt-3 flex gap-2"><button type="button" onClick={() => setShowComparison((value) => !value)} className="rounded border border-zinc-600 px-2 py-1 text-xs text-zinc-100">{showComparison ? "收起比較" : "比較 A/B"}</button><button type="button" onClick={accept} className="rounded bg-emerald-300 px-2 py-1 text-xs font-semibold text-zinc-950">✓ 採納</button><button type="button" onClick={() => { clearProposal(); setShowComparison(false); }} className="rounded border border-zinc-600 px-2 py-1 text-xs text-zinc-300">捨棄</button></div>{showComparison && <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]"><div className="rounded bg-zinc-950 p-2 text-zinc-400">A · 目前<br /><b className="text-zinc-100">{snapshot.clips.length}</b> 個片段</div><div className="rounded bg-fuchsia-950/45 p-2 text-fuchsia-100">B · AI 提案<br /><b>{proposal.snapshot.clips.length}</b> 個片段</div></div>}</div>}
  </aside>;
}
