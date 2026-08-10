"use client";

export function ScopesWorkspaceModule() {
  return (
    <section className="min-h-40 rounded-2xl border border-emerald-400/20 bg-zinc-950 p-4 shadow-xl">
      <div className="flex items-center justify-between"><h2 className="text-sm font-semibold">Video Scopes</h2><span className="text-xs text-emerald-300">Waveform · RGB Parade</span></div>
      <div className="relative mt-3 h-28 overflow-hidden rounded-lg border border-zinc-800 bg-[linear-gradient(to_right,transparent_24%,#27272a_25%,transparent_26%,transparent_49%,#27272a_50%,transparent_51%,transparent_74%,#27272a_75%,transparent_76%),linear-gradient(to_bottom,transparent_24%,#27272a_25%,transparent_26%,transparent_49%,#27272a_50%,transparent_51%,transparent_74%,#27272a_75%,transparent_76%)]">
        <svg viewBox="0 0 400 100" className="absolute inset-0 h-full w-full" preserveAspectRatio="none"><path d="M0 70 C35 15 60 88 95 43 S155 80 195 30 S260 75 300 42 S365 70 400 20" fill="none" stroke="#34d399" strokeOpacity=".9" strokeWidth="1.5" /></svg>
      </div>
    </section>
  );
}
