"use client";

import { useEffect, useRef, useState } from "react";

import { getAudioMixEngine, type MeterReading, type MixBusId } from "@/features/editor/audio-mix-engine";
import { useTimelineStore } from "@/features/editor/timeline-store";

const BUSES: Array<{ id: Exclude<MixBusId, "master">; label: string; tone: string; defaultDb: number; detail: string }> = [
  { id: "dialogue", label: "DIALOGUE", tone: "#67e8f9", defaultDb: 0, detail: "HPF 75Hz · Presence 3.2k · Comp 3.5:1" },
  { id: "music", label: "MUSIC", tone: "#c4b5fd", defaultDb: -12, detail: "側鏈由 Dialogue Bus 驅動" },
  { id: "sfx", label: "SFX", tone: "#fbbf24", defaultDb: -6, detail: "效果音／環境聲" },
];

function db(value: number) { return 20 * Math.log10(Math.max(.00001, value)); }

/** 60fps DOM updates stay outside React state; peak hold and clipping LED are driven by AudioWorklet readings. */
function VuMeter({ bus, tone }: { bus: MixBusId; tone: string }) {
  const fillRef = useRef<HTMLDivElement>(null); const peakRef = useRef<HTMLDivElement>(null); const clipRef = useRef<HTMLDivElement>(null); const readingRef = useRef<MeterReading>({ rms: 0, peak: 0, clipped: false, timestamp: 0 });
  useEffect(() => {
    const engine = getAudioMixEngine(); const unsubscribe = engine.subscribe((incomingBus, reading) => { if (incomingBus === bus) readingRef.current = reading; }); let frame = 0;
    const draw = () => { const reading = readingRef.current; const level = Math.max(0, Math.min(1, (db(reading.rms) + 60) / 60)); const peak = Math.max(0, Math.min(1, (db(reading.peak) + 60) / 60)); if (fillRef.current) fillRef.current.style.transform = `scaleY(${level})`; if (peakRef.current) peakRef.current.style.bottom = `${peak * 100}%`; if (clipRef.current) clipRef.current.style.opacity = reading.clipped ? "1" : ".22"; frame = requestAnimationFrame(draw); }; frame = requestAnimationFrame(draw); return () => { cancelAnimationFrame(frame); unsubscribe(); };
  }, [bus]);
  return <div className="relative h-40 w-5 overflow-hidden rounded-sm border border-zinc-700 bg-zinc-950 shadow-inner"><div className="absolute inset-0 opacity-40 [background-image:repeating-linear-gradient(to_top,transparent_0_8px,#18181b_8px_10px)]" /><div ref={fillRef} className="absolute inset-x-0 bottom-0 origin-bottom bg-gradient-to-t from-emerald-400 via-lime-300 to-red-400" style={{ transform: "scaleY(0)" }} /><div ref={peakRef} className="absolute left-0 right-0 h-[2px] bg-white shadow-[0_0_5px_white]" style={{ bottom: 0 }} /><div ref={clipRef} className="absolute left-1/2 top-1 h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-red-400 shadow-[0_0_8px_#f43f5e]" title="Peak hold / clipping" /></div>;
}

export function AudioMixerWorkspaceModule() {
  const clips = useTimelineStore((state) => state.clips); const [levels, setLevels] = useState<Record<Exclude<MixBusId, "master">, number>>({ dialogue: 0, music: -12, sfx: -6 }); const [enabled, setEnabled] = useState(false); const [ducking, setDucking] = useState(true); const [message, setMessage] = useState("尚未啟用瀏覽器預覽混音圖。");
  const routes = getAudioMixEngine().routeTimeline(clips);
  const enable = async () => { try { await getAudioMixEngine().initialise(); BUSES.forEach((bus) => getAudioMixEngine().setBusGain(bus.id, levels[bus.id])); getAudioMixEngine().setDucking(ducking); setEnabled(true); setMessage("Bus graph 已啟用：所有來源可路由到 Dialogue／Music／SFX，再匯入 Master。"); } catch { setMessage("此瀏覽器無法啟用 Web Audio 混音。 "); } };
  return <section className="rounded-2xl border border-violet-400/25 bg-[linear-gradient(145deg,#18181b,#09090b)] p-4 shadow-xl"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-sm font-semibold tracking-[.14em] text-zinc-100">MIX CONSOLE</h2><p className="mt-1 text-xs text-zinc-500">Bus routing · EQ · Compression · Sidechain ducking</p></div><button type="button" onClick={() => void enable()} className={`rounded-lg px-3 py-2 text-xs font-bold ${enabled ? "bg-emerald-300 text-zinc-950" : "bg-violet-300 text-zinc-950"}`}>{enabled ? "● Audio Graph Online" : "啟用預覽混音"}</button></div>
    <div className="mt-3 flex items-center justify-between rounded-lg border border-zinc-800 bg-black/30 px-3 py-2 text-[10px]"><span className="text-zinc-400">目前路由 {routes.length} 條音訊片段</span><label className="flex items-center gap-2 text-zinc-200"><input type="checkbox" checked={ducking} onChange={(event) => { setDucking(event.target.checked); getAudioMixEngine().setDucking(event.target.checked); }} className="accent-violet-300" />Dialogue → Music Sidechain</label><button type="button" disabled={!enabled} onClick={() => getAudioMixEngine().setDialogueTone(true)} className="text-cyan-200 disabled:opacity-40">測試對白訊號</button></div>
    <div className="mt-4 grid grid-cols-3 gap-2">{BUSES.map((bus) => <article key={bus.id} className="rounded-xl border border-zinc-700/80 bg-zinc-900/70 p-3 shadow-[inset_0_1px_rgba(255,255,255,.05)]"><div className="flex items-start justify-between gap-2"><div><h3 className="text-[11px] font-black tracking-[.16em]" style={{ color: bus.tone }}>{bus.label}</h3><p className="mt-1 min-h-8 text-[9px] leading-3 text-zinc-500">{bus.detail}</p></div><VuMeter bus={bus.id} tone={bus.tone} /></div><div className="mt-3 flex items-end justify-between gap-2"><div className="relative h-36 w-8 rounded-full border border-zinc-700 bg-gradient-to-r from-zinc-950 via-zinc-700 to-zinc-950 p-1 shadow-inner"><input aria-label={`${bus.label} fader`} disabled={!enabled} type="range" min="-60" max="12" step="0.1" value={levels[bus.id]} onChange={(event) => { const value = Number(event.target.value); setLevels((current) => ({ ...current, [bus.id]: value })); getAudioMixEngine().setBusGain(bus.id, value); }} className="absolute left-1/2 top-1/2 h-6 w-32 -translate-x-1/2 -translate-y-1/2 -rotate-90 accent-zinc-200 disabled:opacity-40" /></div><output className="pb-1 text-xs font-mono text-zinc-200">{levels[bus.id].toFixed(1)}<small className="ml-0.5 text-zinc-500">dB</small></output></div></article>)}</div>
    <div className="mt-3 rounded-lg border border-zinc-800 bg-black/30 px-3 py-2 text-[10px] text-zinc-500">Master Bus：Brickwall compressor（−1 dBTP）→ AudioDestination。輸出渲染仍由後端 FFmpeg 混音管線套用等效設定。</div>{message && <p className="mt-2 text-[10px] text-violet-100">{message}</p>}
  </section>;
}
