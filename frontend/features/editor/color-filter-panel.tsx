"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { COLOR_FILTER_PRESETS, drawPresetFilter, type PresetFilterId } from "@/features/editor/preset-filter-canvas";
import { useOptimisticEffectsStore } from "@/features/editor/optimistic-effects-store";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface ColorFilterPanelProps {
  timelineId: string;
  userId: string;
  /** A video, image, or source canvas owned by the editor preview. */
  previewSource: CanvasImageSource | null;
  previewFrameVersion?: number;
  /** Optional binding makes the target Clip glow immediately in the Timeline. */
  clipId?: string;
  mediaAssetId?: string;
  className?: string;
  onApplied?: (selection: { presetId: PresetFilterId; intensity: number }) => void;
  onReverted?: () => void;
}

export function ColorFilterPanel({ timelineId, previewSource, previewFrameVersion = 0, clipId, mediaAssetId, className = "", onApplied, onReverted }: ColorFilterPanelProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [selected, setSelected] = useState<PresetFilterId>("vintage_film");
  const [hovered, setHovered] = useState<PresetFilterId | null>(null);
  const [intensity, setIntensity] = useState(70);
  const [pending, setPending] = useState(false); const [error, setError] = useState<string | null>(null); const [saved, setSaved] = useState(false);
  const beginOptimistic = useOptimisticEffectsStore((state) => state.begin);
  const completeOptimistic = useOptimisticEffectsStore((state) => state.complete);
  const failOptimistic = useOptimisticEffectsStore((state) => state.fail);
  const activePreset = hovered ?? selected;
  const activeDefinition = useMemo(() => COLOR_FILTER_PRESETS.find((preset) => preset.id === activePreset)!, [activePreset]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !previewSource) return;
    drawPresetFilter(canvas, previewSource, activePreset, intensity);
  }, [previewSource, previewFrameVersion, activePreset, intensity]);

  const apply = async () => {
    // Commit the edit metadata and visual feedback before the network request. The user can
    // keep editing other Clips while the server persists or renders the authoritative version.
    const selection = { presetId: selected, intensity };
    const optimisticId = beginOptimistic({ kind: "filter", clipId, mediaAssetId, message: `${activeDefinition.name} 已先套用預覽。` });
    setPending(true); setError(null); setSaved(true); onApplied?.(selection);
    try {
      const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/color-filter`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ preset_id: selected, intensity }) });
      const payload = await response.json() as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "無法套用濾鏡");
      completeOptimistic(optimisticId, `${activeDefinition.name} 已完成同步。`);
    } catch (cause) { const message = cause instanceof Error ? cause.message : "無法套用濾鏡"; failOptimistic(optimisticId, message); setSaved(false); onReverted?.(); setError(message); }
    finally { setPending(false); }
  };

  return <section className={`rounded-xl border border-zinc-800 bg-zinc-950 p-4 ${className}`}><div className="flex items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-zinc-100">一鍵質感濾鏡</h2><p className="mt-1 text-xs text-zinc-400">滑過風格即可即時試看；套用後會在最終導出中燒錄。</p></div><span className="rounded-full px-2 py-1 text-[10px] font-semibold text-zinc-950" style={{ backgroundColor: activeDefinition.accent }}>{hovered ? "預覽中" : "已選擇"}</span></div><div className="mt-3 overflow-hidden rounded-lg bg-black"><canvas ref={canvasRef} width={480} height={270} aria-label={`${activeDefinition.name} 濾鏡預覽`} className="aspect-video w-full object-contain" /></div><p className="mt-2 text-xs text-zinc-300">{activeDefinition.name} · {activeDefinition.description}</p><div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">{COLOR_FILTER_PRESETS.map((preset) => <button key={preset.id} type="button" onMouseEnter={() => setHovered(preset.id)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(preset.id)} onBlur={() => setHovered(null)} onClick={() => { setSelected(preset.id); setSaved(false); }} aria-pressed={selected === preset.id} className={`rounded-lg border p-2 text-left transition ${selected === preset.id ? "border-white bg-zinc-800" : "border-zinc-800 bg-zinc-900 hover:border-zinc-600"}`}><span className="block h-7 rounded" style={{ background: `linear-gradient(135deg, ${preset.accent}, #151515)` }} /><span className="mt-1 block text-[11px] font-medium text-zinc-100">{preset.name}</span></button>)}</div><label className="mt-4 block text-xs text-zinc-300">濃度 <span className="font-semibold text-white">{intensity}%</span><input type="range" min="0" max="100" value={intensity} onChange={(event) => { setIntensity(Number(event.target.value)); setSaved(false); }} className="mt-2 w-full accent-white" /></label><button type="button" onClick={() => void apply()} className="mt-3 rounded bg-white px-3 py-1.5 text-xs font-bold text-zinc-950">{pending ? "已先套用，雲端同步中…" : "套用到影片"}</button>{saved && <p className="mt-2 text-xs text-emerald-300">已先套用；你可以繼續剪輯，最終輸出會使用此濾鏡。</p>}{error && <p role="alert" className="mt-2 text-xs text-red-300">{error}</p>}</section>;
}
