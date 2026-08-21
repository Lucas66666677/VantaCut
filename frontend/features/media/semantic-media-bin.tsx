"use client";

import { FormEvent, useState } from "react";

import { authenticatedFetch } from "@/lib/api/authenticated-fetch";
import { useTimelineStore } from "@/features/editor/timeline-store";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface SearchResult {
  media_asset_id: string;
  filename: string;
  source_duration?: number;
  source_start: number;
  source_end: number;
  modality: string;
  similarity_score: number;
  matched_text?: string;
}

export function SemanticMediaBin({ projectId }: { projectId?: string }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const addSemanticSearchClip = useTimelineStore((state) => state.addSemanticSearchClip);

  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!projectId || query.trim().length < 2) return;
    setPending(true); setMessage(null);
    try {
      const response = await authenticatedFetch(`${API_URL}/api/v1/media/search`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_id: projectId, query: query.trim(), limit: 12 }) });
      const body = await response.json() as { results?: SearchResult[]; detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "無法搜尋素材");
      setResults(body.results ?? []);
      if (!body.results?.length) setMessage("找不到符合的已分析素材。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "無法搜尋素材"); }
    finally { setPending(false); }
  };

  return (
    <section aria-labelledby="semantic-media-title" className="border border-[var(--lr-color-border)] bg-[var(--lr-color-surface)] p-3">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end"><div><h2 id="semantic-media-title" className="text-sm font-semibold">語意素材庫</h2><p className="mt-1 text-xs text-[var(--lr-color-text-muted)]">用畫面內容或對白搜尋已分析素材。</p></div><form onSubmit={search} className="flex min-w-0 gap-2 sm:w-[28rem]"><label htmlFor="semantic-media-query" className="sr-only">搜尋素材</label><input id="semantic-media-query" value={query} onChange={(event) => setQuery(event.target.value)} disabled={!projectId} minLength={2} className="min-w-0 flex-1 rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border)] bg-[var(--lr-color-background)] px-3 py-2 text-xs outline-none focus:border-[var(--lr-color-primary)] disabled:cursor-not-allowed disabled:opacity-50" placeholder={projectId ? "例如：城市夜景、掌聲或產品特寫" : "建立或開啟專案後即可搜尋"} /><button disabled={!projectId || query.trim().length < 2 || pending} className="rounded-[var(--lr-radius-sm)] border border-[var(--lr-color-border-strong)] bg-[var(--lr-color-surface-raised)] px-3 py-2 text-xs font-medium disabled:opacity-50">{pending ? "搜尋中" : "搜尋"}</button></form></div>
      {message && <p role="status" className="mt-3 text-xs text-[var(--lr-color-text-muted)]">{message}</p>}
      {results.length > 0 && <ul className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{results.map((result) => <li key={`${result.media_asset_id}:${result.source_start}`} className="border border-[var(--lr-color-border)] bg-[var(--lr-color-surface-raised)] p-3"><div className="flex items-start justify-between gap-2"><span className="truncate text-xs font-medium" title={result.filename}>{result.filename}</span><span className="font-mono text-[10px] text-[var(--lr-color-secondary)]">{Math.round(result.similarity_score * 100)}%</span></div><p className="mt-2 line-clamp-2 min-h-8 text-[10px] leading-4 text-[var(--lr-color-text-muted)]">{result.matched_text || result.modality}</p><button type="button" onClick={() => addSemanticSearchClip({ id: result.media_asset_id, sourceStart: result.source_start, sourceEnd: result.source_end, sourceDuration: result.source_duration, filename: result.filename })} className="mt-3 w-full rounded-[var(--lr-radius-sm)] bg-[var(--lr-color-primary-soft)] px-2 py-1.5 text-[10px] font-semibold text-[var(--lr-color-primary-strong)] hover:bg-[var(--lr-color-primary)] hover:text-[var(--lr-color-text-inverse)]">加入目前時間點</button></li>)}</ul>}
    </section>
  );
}
