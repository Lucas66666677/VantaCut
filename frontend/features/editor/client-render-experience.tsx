"use client";

import { AnimatePresence, motion } from "framer-motion";

import type { ClientRenderProgress } from "@/types/client-render";

export function ClientRenderExperience({ progress, thumbnail, active }: { progress: ClientRenderProgress | null; thumbnail: string | null; active: boolean }) {
  const percentage = Math.round((progress?.progress ?? 0) * 100);
  const slow = active && (progress?.phase === "loading" || progress?.phase === "concatenating");
  const complete = progress?.phase === "completed";
  return <AnimatePresence>{(active || complete) && <motion.section initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 10 }} className="relative mt-3 overflow-hidden rounded-xl border border-cyan-200/20 bg-zinc-950/90 p-3 shadow-2xl">
    <div className="absolute inset-0 opacity-40" style={{ background: "radial-gradient(circle at 24% 10%, rgba(34,211,238,.2), transparent 42%), radial-gradient(circle at 83% 90%, rgba(168,85,247,.18), transparent 38%)" }} />
    <div className="relative flex gap-3">
      <div className="relative h-20 w-36 shrink-0 overflow-hidden rounded-lg border border-white/10 bg-zinc-900">
        {thumbnail ? <img src={thumbnail} alt="正在編碼的影格預覽" className="h-full w-full object-cover opacity-80 transition-opacity duration-200" /> : <div className="h-full w-full animate-pulse bg-gradient-to-br from-zinc-800 to-zinc-950" />}
        <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1.5 py-0.5 text-[9px] text-cyan-100">LIVE · 5 FPS</span>
      </div>
      <div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><p className="text-xs font-semibold text-zinc-100">{complete ? "導出完成，準備起飛" : progress?.phase === "concatenating" ? "正在無縫封裝片段" : "正在把時間軸煉成影片"}</p><span className="text-sm font-black text-cyan-200">{percentage}%</span></div><p className="mt-1 text-[10px] text-zinc-400">{progress?.runtime === "multi-thread" ? "多執行緒 WASM 正在全速編碼" : "正在使用瀏覽器安全渲染核心"}</p>
        <div className="relative mt-3 h-3 overflow-hidden rounded-full bg-zinc-800"><motion.div className="absolute inset-y-0 left-0 rounded-full" animate={{ width: `${percentage}%` }} transition={{ type: "spring", stiffness: 95, damping: 22 }} style={{ background: "linear-gradient(90deg,#22d3ee,#818cf8,#e879f9)" }}><motion.span className="absolute inset-0 opacity-70" animate={{ x: slow ? ["-35%", "120%"] : ["-12%", "110%"] }} transition={{ repeat: Infinity, duration: slow ? .8 : 1.7, ease: "linear" }} style={{ background: "linear-gradient(110deg,transparent,rgba(255,255,255,.82),transparent)" }} /></motion.div></div>
      </div>
    </div>
    {complete && <div className="pointer-events-none absolute inset-0">{Array.from({ length: 18 }, (_, index) => <motion.i key={index} initial={{ x: "50%", y: "55%", opacity: 1, scale: 1 }} animate={{ x: `${8 + (index * 37) % 90}%`, y: `${12 + (index * 53) % 68}%`, opacity: 0, scale: .3, rotate: index * 46 }} transition={{ duration: .9, ease: "easeOut" }} className="absolute h-1.5 w-1.5 rounded-sm bg-cyan-200" />)}</div>}
  </motion.section>}</AnimatePresence>;
}
