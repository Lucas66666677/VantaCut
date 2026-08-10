"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { create } from "zustand";

import type { ClipLayout } from "@/types/timeline";

export type EditorCommand = "split" | "speed" | "matting" | "text_font" | "text_animation" | "text_color" | "noir" | "noise_reduction";

const COMMANDS: Array<{ id: EditorCommand; label: string; detail: string; shortcut?: string; keywords: string[] }> = [
  { id: "split", label: "分割片段", detail: "在播放頭切開目前片段", shortcut: "⌘ B", keywords: ["split", "分割", "切開"] },
  { id: "speed", label: "曲線變速", detail: "套用電影感節奏預設", shortcut: "⌘ ⌥ R", keywords: ["speed", "變速", "速度"] },
  { id: "matting", label: "AI 一鍵去背", detail: "建立人物 Alpha 遮罩", shortcut: "⌘ ⇧ M", keywords: ["matting", "去背", "摳像", "背景"] },
  { id: "noir", label: "黑白電影感", detail: "降低飽和度並提高對比", shortcut: "⌘ ⌥ B", keywords: ["noir", "黑白", "電影", "濾鏡"] },
  { id: "noise_reduction", label: "AI 降噪", detail: "強化人聲並降低背景底噪", shortcut: "⌘ ⇧ N", keywords: ["noise", "降噪", "人聲", "studio"] },
  { id: "text_font", label: "文字字體", detail: "切換高辨識度粗體字", shortcut: "⌘ ⌥ F", keywords: ["font", "字體", "文字"] },
  { id: "text_animation", label: "文字動畫", detail: "套用彈跳入場動畫", shortcut: "⌘ ⌥ A", keywords: ["animation", "動畫", "文字"] },
  { id: "text_color", label: "文字顏色", detail: "套用高對比亮黃色", shortcut: "⌘ ⌥ C", keywords: ["color", "顏色", "文字"] },
];

const useCommandFeedbackStore = create<{ flashing: EditorCommand | null; flash: (command: EditorCommand) => void }>((set) => ({
  flashing: null,
  flash: (command) => { set({ flashing: command }); window.setTimeout(() => set((state) => state.flashing === command ? { flashing: null } : state), 180); },
}));

function ShortcutButton({ command, children, onExecute }: { command: EditorCommand; children: React.ReactNode; onExecute: (command: EditorCommand) => void }) {
  const flashing = useCommandFeedbackStore((state) => state.flashing === command);
  const flash = useCommandFeedbackStore((state) => state.flash);
  const item = COMMANDS.find((entry) => entry.id === command)!;
  return (
    <div className="group relative">
      <button
        type="button"
        data-editor-command={command}
        onClick={() => { flash(command); onExecute(command); }}
        className={`rounded-md px-2 py-1.5 text-xs font-medium text-zinc-100 transition hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200/80 ${flashing ? "scale-110 bg-cyan-200/20 text-cyan-50" : ""}`}
      >
        {children}
      </button>
      <div role="tooltip" className="pointer-events-none absolute left-1/2 top-[calc(100%+.45rem)] z-[70] hidden w-max -translate-x-1/2 rounded-md border border-white/10 bg-zinc-950 px-2 py-1 text-[10px] text-zinc-300 shadow-xl group-hover:block">
        {item.label}{item.shortcut && <kbd className="ml-2 rounded border border-zinc-600 bg-zinc-800 px-1 py-0.5 font-sans text-[9px] text-zinc-100">{item.shortcut}</kbd>}
      </div>
    </div>
  );
}

const isTextClip = (clip: ClipLayout) => clip.kind?.includes("text") || clip.kind?.includes("caption") || Boolean(clip.text_style);

export function ContextualFloatingToolbar({ clip, anchor, onExecute, onClose }: { clip: ClipLayout; anchor: { x: number; y: number }; onExecute: (command: EditorCommand, clip: ClipLayout) => void; onClose: () => void }) {
  const commands = isTextClip(clip) ? ["text_font", "text_animation", "text_color"] as const : ["split", "speed", "matting"] as const;
  return (
    <div
      role="toolbar"
      aria-label="片段快速工具列"
      onPointerDown={(event) => event.stopPropagation()}
      className="absolute z-[60] flex -translate-x-1/2 -translate-y-[calc(100%+10px)] items-center gap-0.5 rounded-xl border border-white/20 bg-zinc-950/75 p-1 shadow-2xl shadow-black/40 backdrop-blur-xl"
      style={{ left: anchor.x, top: anchor.y }}
    >
      {commands.map((command) => <ShortcutButton key={command} command={command} onExecute={(id) => onExecute(id, clip)}>{({ split: "✂ 分割", speed: "↗ 變速", matting: "◌ 去背", text_font: "Aa 字體", text_animation: "✦ 動畫", text_color: "● 顏色" } as Record<EditorCommand, string>)[command]}</ShortcutButton>)}
      <span className="mx-0.5 h-4 w-px bg-white/15" />
      <button type="button" aria-label="關閉快速工具列" onClick={onClose} className="grid h-6 w-6 place-items-center rounded-md text-zinc-400 transition hover:bg-white/10 hover:text-white">×</button>
    </div>
  );
}

export function EditorKeyboardManager({ onCommand, onOpenPalette }: { onCommand: (command: EditorCommand) => void; onOpenPalette: () => void }) {
  const flash = useCommandFeedbackStore((state) => state.flash);
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, [contenteditable='true']")) return;
      const modifier = event.metaKey || event.ctrlKey;
      const key = event.key.toLowerCase();
      if (modifier && key === "k") { event.preventDefault(); onOpenPalette(); return; }
      const command = modifier && !event.altKey && key === "b" ? "split"
        : modifier && event.altKey && key === "r" ? "speed"
          : modifier && event.shiftKey && key === "m" ? "matting"
            : modifier && event.shiftKey && key === "n" ? "noise_reduction"
              : modifier && event.altKey && key === "b" ? "noir"
                : modifier && event.altKey && key === "f" ? "text_font"
                  : modifier && event.altKey && key === "a" ? "text_animation"
                    : modifier && event.altKey && key === "c" ? "text_color" : null;
      if (!command) return;
      event.preventDefault(); flash(command); onCommand(command);
    };
    window.addEventListener("keydown", listener); return () => window.removeEventListener("keydown", listener);
  }, [flash, onCommand, onOpenPalette]);
  return null;
}

export function EditorCommandPalette({ open, onOpenChange, onExecute }: { open: boolean; onOpenChange: (open: boolean) => void; onExecute: (command: EditorCommand) => void }) {
  const [query, setQuery] = useState(""); const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) { setQuery(""); window.setTimeout(() => inputRef.current?.focus(), 0); } }, [open]);
  const results = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return !normalized ? COMMANDS : COMMANDS.filter((command) => [command.label, command.detail, ...command.keywords].some((value) => value.toLowerCase().includes(normalized)));
  }, [query]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[100] grid place-items-start bg-black/35 px-4 pt-[18vh] backdrop-blur-[2px]" onPointerDown={() => onOpenChange(false)}>
      <section role="dialog" aria-modal="true" aria-label="命令面板" onPointerDown={(event) => event.stopPropagation()} className="w-full max-w-xl overflow-hidden rounded-2xl border border-white/20 bg-zinc-950/85 shadow-2xl shadow-black/50 backdrop-blur-2xl">
        <div className="flex items-center gap-3 border-b border-white/10 px-4"><span className="text-cyan-200">⌘</span><input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") onOpenChange(false); if (event.key === "Enter" && results[0]) { onExecute(results[0].id); onOpenChange(false); } }} placeholder="輸入「黑白」、「降噪」或動作名稱…" className="h-12 min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-zinc-500" /><kbd className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-[10px] text-zinc-400">ESC</kbd></div>
        <div className="max-h-80 overflow-y-auto p-2">{results.map((command) => <button key={command.id} type="button" onClick={() => { onExecute(command.id); onOpenChange(false); }} className="flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition hover:bg-white/10"><span className="grid h-7 w-7 place-items-center rounded-lg bg-cyan-400/10 text-cyan-100">⌁</span><span className="min-w-0 flex-1"><span className="block text-sm text-zinc-100">{command.label}</span><span className="block text-xs text-zinc-500">{command.detail}</span></span>{command.shortcut && <kbd className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-[10px] text-zinc-300">{command.shortcut}</kbd>}</button>)}{results.length === 0 && <p className="px-3 py-8 text-center text-sm text-zinc-500">找不到命令；試試「黑白」或「降噪」。</p>}</div>
      </section>
    </div>
  );
}
