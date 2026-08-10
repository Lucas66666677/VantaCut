"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { create } from "zustand";

const STORAGE_KEY = "editor-av-latency-compensation-ms";
const TRIALS = 5;

interface LatencyState { compensationMs: number; calibratedAt?: number; setCompensation: (milliseconds: number) => void; }
export const useAvLatencyStore = create<LatencyState>((set) => ({
  compensationMs: 0,
  setCompensation: (milliseconds) => {
    const compensationMs = Math.max(0, Math.min(600, Math.round(milliseconds)));
    if (typeof window !== "undefined") localStorage.setItem(STORAGE_KEY, String(compensationMs));
    set({ compensationMs, calibratedAt: Date.now() });
  },
}));

/** Read imperatively from audio/render loops; no React subscription on the hot path. */
export function avLatencyCompensationSeconds(): number { return useAvLatencyStore.getState().compensationMs / 1_000; }

function makeContext(): AudioContext {
  const Context = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  return new Context({ latencyHint: "interactive" });
}

function playBeep(context: AudioContext): void {
  const oscillator = context.createOscillator(); const gain = context.createGain(); const when = context.currentTime + .045;
  oscillator.type = "sine"; oscillator.frequency.setValueAtTime(880, when); gain.gain.setValueAtTime(.0001, when); gain.gain.exponentialRampToValueAtTime(.18, when + .006); gain.gain.exponentialRampToValueAtTime(.0001, when + .07);
  oscillator.connect(gain); gain.connect(context.destination); oscillator.start(when); oscillator.stop(when + .08);
}

type Phase = "idle" | "ready" | "waiting" | "complete";

/** Manual A/V calibration: visual reaction reference is subtracted from five beep+flash responses. */
export function AvLatencyCalibrationSettings() {
  const compensationMs = useAvLatencyStore((state) => state.compensationMs); const setCompensation = useAvLatencyStore((state) => state.setCompensation);
  const [open, setOpen] = useState(false); const [phase, setPhase] = useState<Phase>("idle"); const [flash, setFlash] = useState(false); const [samples, setSamples] = useState<number[]>([]); const [deviceChanged, setDeviceChanged] = useState(false);
  const contextRef = useRef<AudioContext | null>(null); const flashedAtRef = useRef(0); const timerRef = useRef<number | null>(null);
  const armTrial = useCallback(() => {
    timerRef.current = window.setTimeout(() => {
      flashedAtRef.current = performance.now(); setFlash(true); playBeep(contextRef.current!); setPhase("waiting");
      window.setTimeout(() => setFlash(false), 105);
    }, 650);
  }, []);
  const start = useCallback(async () => {
    contextRef.current?.close(); contextRef.current = makeContext(); await contextRef.current.resume(); setSamples([]); setPhase("ready"); armTrial();
  }, [armTrial]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.code !== "Space" || phase !== "waiting") return;
      event.preventDefault(); const responseMs = performance.now() - flashedAtRef.current;
      // Normal human visual reaction is measured around 220 ms. The calibration is a perceptual offset,
      // not a false claim of laboratory hardware latency; clamp rejects accidental key holds.
      if (responseMs < 80 || responseMs > 1_200) return;
      const next = [...samples, responseMs]; setSamples(next); setPhase("ready");
      if (next.length >= TRIALS) {
        const sorted = [...next].sort((a, b) => a - b); const trimmed = sorted.slice(1, -1);
        const perceptualDelay = Math.max(0, trimmed.reduce((sum, value) => sum + value, 0) / trimmed.length - 220);
        setCompensation(perceptualDelay); setPhase("complete"); return;
      }
      armTrial();
    };
    window.addEventListener("keydown", onKey, { capture: true }); return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [armTrial, phase, samples, setCompensation]);
  useEffect(() => {
    const saved = Number(localStorage.getItem(STORAGE_KEY)); if (Number.isFinite(saved) && saved > 0) useAvLatencyStore.getState().setCompensation(saved);
    const onDeviceChange = () => { void navigator.mediaDevices?.enumerateDevices().then((devices) => { if (devices.some((device) => device.kind === "audiooutput")) setDeviceChanged(true); }); };
    navigator.mediaDevices?.addEventListener("devicechange", onDeviceChange); return () => { navigator.mediaDevices?.removeEventListener("devicechange", onDeviceChange); if (timerRef.current) clearTimeout(timerRef.current); void contextRef.current?.close(); };
  }, []);
  return <div className="relative">
    <button type="button" onClick={() => setOpen((value) => !value)} className="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300">系統設定</button>
    {open && <section className="absolute right-0 top-11 z-50 w-80 rounded-xl border border-zinc-700 bg-zinc-950 p-4 shadow-2xl">
      <h2 className="text-sm font-semibold text-zinc-100">A/V 同步校準</h2><p className="mt-1 text-xs leading-5 text-zinc-400">按下開始後，畫面會閃光並發出嗶聲；每次聽到嗶聲立即按空白鍵，共 {TRIALS} 次。</p>
      <div className={`mt-3 grid h-20 place-items-center rounded-lg transition ${flash ? "bg-white shadow-[0_0_36px_white]" : "bg-zinc-900"}`}><span className={flash ? "text-zinc-950" : "text-zinc-500"}>{phase === "waiting" ? "聽到嗶聲請按空白鍵" : phase === "complete" ? "校準完成" : "準備中"}</span></div>
      <div className="mt-3 flex items-center justify-between text-xs"><span className="text-zinc-400">已收集 {samples.length}/{TRIALS}</span><strong className="text-cyan-200">🎧 +{compensationMs}ms</strong></div>
      <button type="button" onClick={() => void start()} className="mt-3 w-full rounded-lg bg-cyan-300 px-3 py-2 text-xs font-bold text-zinc-950">{phase === "waiting" || phase === "ready" ? "重新開始校準" : "開始校準"}</button>
      <p className="mt-2 text-[10px] leading-4 text-zinc-500">此為感知延遲估計，已扣除一般反應時間；藍牙裝置切換後建議重新校準。</p>
    </section>}
    {deviceChanged && <div className="absolute right-0 top-12 z-[60] w-72 rounded-xl border border-cyan-200/30 bg-zinc-900/95 p-3 text-xs text-zinc-200 shadow-xl backdrop-blur"><b>偵測到音訊裝置變更</b><p className="mt-1 text-zinc-400">藍牙耳機可能有不同延遲，是否重新校準？</p><div className="mt-2 flex gap-2"><button onClick={() => { setDeviceChanged(false); setOpen(true); }} className="rounded bg-cyan-300 px-2 py-1 font-semibold text-zinc-950">重新校準</button><button onClick={() => setDeviceChanged(false)} className="rounded border border-zinc-600 px-2 py-1">稍後</button></div></div>}
  </div>;
}

export function AvLatencyIndicator() {
  const compensationMs = useAvLatencyStore((state) => state.compensationMs);
  return <span title="Audio output latency compensation" className={`rounded-full border px-2 py-1 text-[10px] ${compensationMs > 0 ? "border-cyan-300/45 bg-cyan-400/10 text-cyan-100" : "border-zinc-700 text-zinc-500"}`}>🎧 延遲補償: +{compensationMs}ms</span>;
}
