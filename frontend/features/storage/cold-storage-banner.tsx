"use client";

import { useProjectStorage } from "@/features/storage/use-project-storage";

export function ColdStorageBanner({ projectId, userId }: { projectId?: string; userId?: string }) {
  const { storage, loading, error, hydrate } = useProjectStorage(projectId, userId);
  if (!projectId || !userId || !storage || storage.high_quality_render_ready) return null;
  const hydration = storage.active_hydration;
  const progress = hydration?.progress ?? 0;
  return (
    <div className="mb-4 rounded-xl border border-sky-400/40 bg-sky-500/10 px-4 py-3 text-sm text-sky-50">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-medium">高畫質原始素材位於冷庫；Proxy 預覽可正常使用。</p>
          <p className="mt-1 text-xs text-sky-100/80">{hydration?.message ?? "最終高畫質導出前需先調回素材。"}</p>
        </div>
        {!hydration && <button onClick={() => void hydrate()} disabled={loading} className="rounded-md bg-sky-400 px-3 py-1.5 text-xs font-semibold text-slate-950 disabled:opacity-50">{loading ? "啟動中…" : "調回高畫質素材"}</button>}
      </div>
      {hydration && <>
        <div className="mt-3 h-1.5 overflow-hidden rounded bg-sky-950"><div className="h-full bg-sky-300 transition-all" style={{ width: `${progress}%` }} /></div>
        <p className="mt-1 text-xs text-sky-100/80">{progress}% · 預計可用時間：{hydration.estimated_ready_at ? new Date(hydration.estimated_ready_at).toLocaleString() : "約 12 小時"}</p>
      </>}
      {error && <p className="mt-2 text-xs text-red-200">{error}</p>}
    </div>
  );
}
