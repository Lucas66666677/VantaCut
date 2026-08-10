"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";

/** Non-blocking offline reassurance: local crash recovery remains active while cloud sync is unavailable. */
export function OfflineModeToast({ active }: { active: boolean }) {
  return <AnimatePresence>{active && <motion.div role="status" aria-live="polite" initial={{ opacity: 0, y: 18, scale: .96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 12, scale: .97 }} transition={{ type: "spring", stiffness: 420, damping: 30 }} className="fixed bottom-5 right-5 z-[120] flex max-w-sm items-start gap-3 rounded-2xl border border-cyan-100/25 bg-zinc-950/70 px-4 py-3 text-sm text-zinc-100 shadow-2xl shadow-black/35 backdrop-blur-xl"><span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-cyan-300/15 text-cyan-100">☁</span><span><b className="block text-xs text-cyan-100">離線模式已啟動</b><span className="mt-0.5 block text-xs text-zinc-300">您的變更將安全保存在本地記憶體，網路恢復後可再次同步。</span></span></motion.div>}</AnimatePresence>;
}

/** Text input that accepts free-form values, then gently rolls invalid numbers back into a safe interval on blur. */
export function ClampNumberInput({ value, min, max, step = 1, onCommit, ariaLabel }: { value: number; min: number; max: number; step?: number; onCommit: (value: number) => void; ariaLabel: string }) {
  const [draft, setDraft] = useState(String(value)); const [focused, setFocused] = useState(false); const [corrected, setCorrected] = useState(false); const animation = useRef<number | null>(null);
  useEffect(() => { if (!focused) setDraft(String(value)); }, [focused, value]);
  useEffect(() => () => { if (animation.current !== null) cancelAnimationFrame(animation.current); }, []);
  const correct = () => {
    const parsed = Number(draft); const safe = Math.min(max, Math.max(min, Number.isFinite(parsed) ? parsed : value));
    const rounded = Math.round(safe / step) * step;
    if (Number.isFinite(parsed) && parsed === rounded) { setDraft(String(rounded)); onCommit(rounded); return; }
    const from = Number.isFinite(parsed) ? parsed : value; const started = performance.now(); const duration = 180;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - started) / duration); const eased = 1 - Math.pow(1 - progress, 3);
      setDraft(String(Math.round((from + (rounded - from) * eased) * 100) / 100));
      if (progress < 1) animation.current = requestAnimationFrame(tick);
      else { setDraft(String(rounded)); onCommit(rounded); setCorrected(true); window.setTimeout(() => setCorrected(false), 380); }
    };
    animation.current = requestAnimationFrame(tick);
  };
  return <input aria-label={ariaLabel} inputMode="decimal" value={draft} onFocus={() => setFocused(true)} onChange={(event) => setDraft(event.target.value)} onBlur={() => { setFocused(false); correct(); }} className={`w-14 rounded border bg-zinc-950 px-1.5 py-0.5 text-right text-xs tabular-nums outline-none transition ${corrected ? "border-emerald-300 text-emerald-100 shadow-[0_0_12px_rgba(110,231,183,.75)]" : "border-zinc-700 text-zinc-100 focus:border-pink-300"}`} />;
}
