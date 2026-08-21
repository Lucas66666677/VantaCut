"use client";

import { useState } from "react";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface HandoffResponse { preview_url: string; qr_code_data_uri: string; expires_at: string; detail?: string; }

export function MobilePreviewHandoff({ timelineId }: { timelineId: string }) {
  const [handoff, setHandoff] = useState<HandoffResponse | null>(null); const [pending, setPending] = useState(false); const [error, setError] = useState<string | null>(null);
  const create = async () => {
    setPending(true); setError(null);
    try {
      const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/mobile-preview-handoff`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
      const data = await response.json() as HandoffResponse;
      if (!response.ok) throw new Error(data.detail ?? "無法建立手機預覽"); setHandoff(data);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "無法建立手機預覽"); } finally { setPending(false); }
  };
  return <div className="relative"><button type="button" onClick={() => void create()} disabled={pending} className="rounded-lg border border-cyan-400/60 bg-cyan-400/10 px-3 py-2 text-xs font-medium text-cyan-100 disabled:opacity-50">{pending ? "產生中…" : "手機預覽"}</button>{handoff && <div className="absolute right-0 z-20 mt-2 w-64 rounded-xl border border-zinc-700 bg-zinc-950 p-3 shadow-2xl"><img src={handoff.qr_code_data_uri} alt="掃描以在手機預覽目前時間軸" className="mx-auto h-44 w-44 rounded bg-white p-2" /><p className="mt-2 text-center text-xs text-zinc-300">掃描 QR Code，在手機預覽目前粗剪。</p><button type="button" onClick={() => void navigator.clipboard.writeText(handoff.preview_url)} className="mt-2 w-full rounded border border-zinc-700 px-2 py-1.5 text-xs text-zinc-200">複製預覽連結</button><p className="mt-2 text-center text-[10px] text-zinc-500">連結至 {new Date(handoff.expires_at).toLocaleTimeString()} 失效</p></div>}{error && <p role="alert" className="absolute right-0 mt-1 w-56 text-xs text-red-300">{error}</p>}</div>;
}

