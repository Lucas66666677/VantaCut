"use client";

import { useEffect, useState } from "react";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type VariantKey = "landscape" | "vertical" | "square";
interface MatrixVariant { key: VariantKey; aspect_ratio: "16:9" | "9:16" | "1:1"; render_job_id: string; status: string; progress: number; preview_url?: string | null; download_url?: string | null; message?: string | null; }
interface MatrixBatch { batch_id: string; status: string; variants: MatrixVariant[]; zip_download_url?: string | null; zip_status?: string | null; distribution_targets: Array<{ platform: "youtube" | "tiktok"; variant: VariantKey }>; detail?: string; }
interface SocialAccount { id: string; platform: "youtube" | "tiktok"; display_name?: string | null; }

const CARD_STYLE: Record<VariantKey, string> = { landscape: "from-sky-400/20 to-blue-950/30", vertical: "from-fuchsia-400/20 to-violet-950/30", square: "from-emerald-400/20 to-teal-950/30" };
const PLATFORM_LABEL = { youtube: "YouTube", tiktok: "TikTok" } as const;

export function OmnichannelExportCommandCenter({ timelineId, userId }: { timelineId?: string; userId?: string }) {
  const [resolution, setResolution] = useState<"720p" | "1080p">("1080p");
  const [batch, setBatch] = useState<MatrixBatch | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);

  const refresh = async (batchId = batch?.batch_id) => {
    if (!timelineId || !userId || !batchId) return;
    const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/omnichannel-export/${batchId}`);
    if (response.ok) setBatch(await response.json() as MatrixBatch);
  };
  useEffect(() => {
    if (!batch || ["completed", "failed"].includes(batch.status)) return;
    const timer = window.setInterval(() => void refresh(), 1_500); return () => window.clearInterval(timer);
  }, [batch?.batch_id, batch?.status, timelineId, userId]);
  useEffect(() => {
    if (!userId) return;
    authenticatedFetch(`${API_BASE_URL}/api/v1/social/accounts`).then(async (response) => response.ok ? setAccounts(await response.json() as SocialAccount[]) : undefined).catch(() => undefined);
  }, [userId]);

  const start = async () => {
    if (!timelineId || !userId) return;
    setBusy(true); setMessage(null);
    try {
      const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/timelines/${timelineId}/omnichannel-export`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ resolution }) });
      const result = await response.json() as MatrixBatch;
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "無法啟動矩陣匯出");
      setBatch(result);
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : "矩陣匯出啟動失敗"); } finally { setBusy(false); }
  };
  const distribute = async () => {
    if (!timelineId || !userId || !batch) return;
    const posts = await Promise.all(batch.distribution_targets.map(async (target) => {
      const account = accounts.find((item) => item.platform === target.platform); const variant = batch.variants.find((item) => item.key === target.variant);
      if (!account || !variant || variant.status !== "completed") return null;
      const response = await authenticatedFetch(`${API_BASE_URL}/api/v1/social/timelines/${timelineId}/publish`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ social_account_id: account.id, render_job_id: variant.render_job_id, title: "最新影片", description: `已由 Omnichannel Export Matrix 輸出 ${variant.aspect_ratio} 版本。`, visibility: "private" }) });
      return response.ok ? target.platform : null;
    }));
    const published = posts.filter(Boolean); setMessage(published.length ? `已排入 ${published.map((item) => PLATFORM_LABEL[item as "youtube" | "tiktok"]).join("、")} 發布佇列。` : "請先在帳號設定完成 YouTube 或 TikTok OAuth 授權。");
  };

  return <section className="rounded-2xl border border-cyan-300/25 bg-[radial-gradient(circle_at_top_right,rgba(34,211,238,.16),transparent_45%),linear-gradient(145deg,#111827,#09090b)] p-4 shadow-[0_0_40px_rgba(34,211,238,.08)]">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-cyan-50">Omnichannel Export Matrix</h2><p className="mt-1 text-xs text-zinc-400">同一份原始高畫質素材，在雲端平行產出 16:9、9:16 與 1:1；直式與方形會自動追蹤主角。</p></div><div className="flex items-center gap-2"><select value={resolution} onChange={(event) => setResolution(event.target.value as "720p" | "1080p")} className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-100"><option value="1080p">1080p Matrix</option><option value="720p">720p Matrix</option></select><button type="button" disabled={busy || !timelineId || !userId} onClick={() => void start()} className="rounded-lg bg-cyan-200 px-3 py-1.5 text-xs font-bold text-zinc-950 disabled:opacity-40">{busy ? "配置虛擬時間軸…" : "啟動矩陣匯出"}</button></div></div>
    <div className="mt-4 grid gap-3 md:grid-cols-3">{(["landscape", "vertical", "square"] as VariantKey[]).map((key) => { const variant = batch?.variants.find((item) => item.key === key); const progress = variant?.progress ?? 0; return <article key={key} className={`overflow-hidden rounded-xl border border-white/10 bg-gradient-to-br ${CARD_STYLE[key]} p-3`}><div className="flex items-center justify-between"><b className="text-xs text-white">{variant?.aspect_ratio ?? ({ landscape: "16:9", vertical: "9:16", square: "1:1" }[key])}</b><span className="text-[10px] text-zinc-300">{variant?.status ?? "待命"}</span></div><div className="mt-3 grid aspect-video place-items-center overflow-hidden rounded-lg bg-black/60">{variant?.preview_url ? variant.status === "completed" ? <video src={variant.preview_url} muted preload="metadata" className="h-full w-full object-cover" /> : <img src={variant.preview_url} alt={`${key} rendering preview`} className="h-full w-full object-cover opacity-70" /> : <span className="text-[11px] text-zinc-500">等待 Worker</span>}</div><div className="mt-3 flex justify-between text-[10px] text-zinc-300"><span>{variant?.message ?? "虛擬時間軸待派送"}</span><b>{progress}%</b></div><div className="mt-1 h-1.5 overflow-hidden rounded-full bg-black/40"><div className="h-full rounded-full bg-cyan-200 transition-[width] duration-500" style={{ width: `${progress}%` }} /></div>{variant?.download_url && <a href={variant.download_url} className="mt-2 inline-block text-[11px] text-cyan-100 underline">下載此比例</a>}</article>; })}</div>
    <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-white/10 pt-3"><span className="mr-auto text-xs text-zinc-400">ZIP：{batch?.zip_status ?? "尚未建立"}</span>{batch?.zip_download_url && <a href={batch.zip_download_url} className="rounded-lg bg-emerald-300 px-3 py-1.5 text-xs font-bold text-zinc-950">下載全部 ZIP</a>}<button type="button" disabled={!batch || !batch.variants.every((item) => item.status === "completed")} onClick={() => void distribute()} className="rounded-lg border border-fuchsia-300/40 bg-fuchsia-400/10 px-3 py-1.5 text-xs font-semibold text-fuchsia-100 disabled:opacity-40">透過 OAuth 一鍵分發</button></div>
    {message && <p role="status" className="mt-2 text-xs text-cyan-100">{message}</p>}
  </section>;
}
