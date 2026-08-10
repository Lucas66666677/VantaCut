"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
interface Clip { id?: string; source_start: number; source_end: number; source_asset_id?: string; action?: "keep" | "remove"; track?: string; }
interface Manifest { timeline: { clips?: Clip[]; source_asset_id?: string }; assets: { id: string; url: string }[]; detail?: string; }

export function MobilePreviewClient() {
  const token = useSearchParams().get("token"); const video = useRef<HTMLVideoElement>(null); const [manifest, setManifest] = useState<Manifest | null>(null); const [error, setError] = useState<string | null>(null); const [index, setIndex] = useState(0);
  const clips = useMemo(() => (manifest?.timeline.clips ?? []).filter((clip) => clip.action !== "remove" && (!clip.track || clip.track === "main_video")).sort((a, b) => a.source_start - b.source_start), [manifest]);
  const current = clips[index]; const assetUrl = manifest?.assets.find((asset) => asset.id === (current?.source_asset_id ?? manifest?.timeline.source_asset_id))?.url ?? manifest?.assets[0]?.url;
  useEffect(() => { if (!token) { setError("缺少手機預覽 Token"); return; } void (async () => { try { const response = await fetch(`${API_URL}/api/v1/timelines/mobile-preview/${encodeURIComponent(token)}`); const data = await response.json() as Manifest; if (!response.ok) throw new Error(data.detail ?? "無法載入預覽"); setManifest(data); } catch (cause) { setError(cause instanceof Error ? cause.message : "無法載入預覽"); } })(); }, [token]);
  useEffect(() => { const element = video.current; if (!element || !current) return; const seek = () => { element.currentTime = current.source_start; void element.play().catch(() => undefined); }; element.addEventListener("loadedmetadata", seek, { once: true }); if (element.readyState >= 1) seek(); return () => element.removeEventListener("loadedmetadata", seek); }, [assetUrl, current]);
  const advance = () => { if (!current || !video.current) return; if (video.current.currentTime >= current.source_end - .04) { if (index + 1 < clips.length) setIndex((value) => value + 1); else video.current.pause(); } };
  if (error) return <main className="grid min-h-screen place-items-center bg-zinc-950 p-6 text-center text-red-300">{error}</main>;
  return <main className="min-h-screen bg-zinc-950 p-4 text-zinc-100"><h1 className="text-lg font-semibold">手機時間軸預覽</h1><p className="mt-1 text-xs text-zinc-400">依目前雲端草稿播放保留片段。</p>{assetUrl ? <video ref={video} src={assetUrl} controls playsInline onTimeUpdate={advance} className="mt-5 aspect-video w-full rounded-xl bg-black" /> : <p className="mt-8 text-sm text-zinc-400">正在準備 Proxy 預覽…</p>}<p className="mt-3 text-xs text-zinc-400">片段 {clips.length ? `${index + 1} / ${clips.length}` : "—"}</p></main>;
}
