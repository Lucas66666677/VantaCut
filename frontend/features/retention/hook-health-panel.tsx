"use client";

import { useCallback, useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type TrafficLight = "green" | "yellow" | "red";
interface HookMetric { label: string; value: string; passed: boolean }
interface HookReport { score: number; traffic_light: TrafficLight; metrics: HookMetric[]; warnings: string[]; suggestions: string[] }

interface HookHealthPanelProps {
  timelineId: string;
  userId: string;
  onRescued?: (timelineId: string) => void;
}

const LIGHT: Record<TrafficLight, string> = { green: "bg-emerald-400", yellow: "bg-amber-400", red: "bg-rose-500" };
const LABEL: Record<TrafficLight, string> = { green: "開場狀態良好", yellow: "建議優化開場", red: "高流失風險" };

/** Put this in the export modal before the final render action. */
export function HookHealthPanel({ timelineId, userId, onRescued }: HookHealthPanelProps) {
  const [report, setReport] = useState<HookReport>();
  const [loading, setLoading] = useState(false);
  const [rescuing, setRescuing] = useState(false);
  const [error, setError] = useState<string>();

  const check = useCallback(async () => {
    setLoading(true); setError(undefined);
    try {
      const response = await fetch(`${API_URL}/api/v1/analysis/timelines/${timelineId}/hook-check`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId }),
      });
      const body = await response.json() as HookReport & { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "無法完成開場健檢");
      setReport(body);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "無法完成開場健檢"); }
    finally { setLoading(false); }
  }, [timelineId, userId]);

  useEffect(() => { void check(); }, [check]);

  const rescue = async () => {
    setRescuing(true); setError(undefined);
    try {
      const response = await fetch(`${API_URL}/api/v1/analysis/timelines/${timelineId}/hook-rescue`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId }),
      });
      const body = await response.json() as { timeline_id?: string; detail?: string };
      if (!response.ok || !body.timeline_id) throw new Error(body.detail ?? "無法建立 Hook 救援版本");
      onRescued?.(body.timeline_id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "無法建立 Hook 救援版本"); }
    finally { setRescuing(false); }
  };

  if (loading && !report) return <div className="rounded-xl border border-zinc-800 bg-zinc-950 p-4 text-sm text-zinc-400">正在檢查前三秒張力…</div>;
  return <section className="rounded-xl border border-zinc-800 bg-zinc-950 p-4 text-zinc-100">
    <div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold">黃金 Hook 健檢</h2><p className="mt-1 text-xs text-zinc-400">導出前檢查畫面切換、動態花字與人聲。</p></div>{report && <div className="flex items-center gap-2 text-sm font-semibold"><span className={`h-3 w-3 rounded-full ${LIGHT[report.traffic_light]}`} /><span>{report.score} 分</span></div>}</div>
    {report && <><p className={`mt-3 text-sm font-medium ${report.traffic_light === "red" ? "text-rose-300" : report.traffic_light === "yellow" ? "text-amber-200" : "text-emerald-200"}`}>{LABEL[report.traffic_light]}</p><div className="mt-3 grid grid-cols-2 gap-2">{report.metrics.map((metric) => <div key={metric.label} className={`rounded-lg border p-2 text-xs ${metric.passed ? "border-emerald-800/70 bg-emerald-950/20" : "border-rose-900/70 bg-rose-950/20"}`}><span className="block text-zinc-400">{metric.label}</span><span className="font-medium">{metric.value}</span></div>)}</div>{report.warnings.map((warning) => <p key={warning} className="mt-2 text-xs text-amber-200">• {warning}</p>)}{report.suggestions.slice(0, 1).map((suggestion) => <p key={suggestion} className="mt-2 text-xs text-zinc-400">建議：{suggestion}</p>)}</>}
    <div className="mt-4 flex gap-2"><button type="button" onClick={() => void check()} disabled={loading} className="rounded border border-zinc-700 px-3 py-2 text-xs hover:bg-zinc-800 disabled:opacity-50">{loading ? "檢查中…" : "重新檢查"}</button><button type="button" onClick={() => void rescue()} disabled={rescuing || !report} className="rounded bg-fuchsia-500 px-3 py-2 text-xs font-semibold text-zinc-950 hover:bg-fuchsia-400 disabled:opacity-50">{rescuing ? "正在建立救援版…" : "幫我優化開場"}</button></div>
    {error && <p role="alert" className="mt-2 text-xs text-rose-300">{error}</p>}
  </section>;
}
