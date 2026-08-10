"use client";

export interface BehavioralCoachReportData {
  radar: Record<string, number>;
  star: { star_score: number; suggestion: string };
  lowest_confidence_segments: { segment_id: string; source_start: number; source_end: number; coaching_score: number; suggestions: string[] }[];
  limitations: string[];
}

const LABELS: Record<string, string> = { eye_contact: "眼神", posture_openness: "姿態", gesture_openness: "手勢", vocal_stability: "聲音穩定", response_structure: "回應結構" };

export function BehavioralCoachReport({ report, onJumpToSegment }: { report: BehavioralCoachReportData; onJumpToSegment?: (seconds: number) => void }) {
  const entries = Object.entries(report.radar).filter(([key]) => key in LABELS);
  const center = 130; const radius = 88;
  const point = (index: number, score: number) => {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / Math.max(1, entries.length);
    const distance = radius * score / 100;
    return `${center + Math.cos(angle) * distance},${center + Math.sin(angle) * distance}`;
  };
  const polygon = entries.map(([_, score], index) => point(index, score)).join(" ");
  return <section className="rounded-2xl border border-emerald-400/30 bg-zinc-950 p-4 text-sm">
    <div className="mb-3 flex items-baseline justify-between"><h3 className="font-semibold text-emerald-100">演講／面試表現教練</h3><span className="text-xs text-zinc-400">STAR {report.star.star_score}/100</span></div>
    <div className="grid gap-4 md:grid-cols-[260px_1fr]"><svg viewBox="0 0 260 260" className="mx-auto w-full max-w-[260px]" aria-label="表現雷達圖">
      {[.25, .5, .75, 1].map((level) => <polygon key={level} points={entries.map(([_, score], index) => point(index, level * 100)).join(" ")} fill="none" stroke="#3f3f46" />)}
      {entries.map(([key], index) => { const angle = -Math.PI / 2 + index * Math.PI * 2 / entries.length; return <text key={key} x={center + Math.cos(angle) * 112} y={center + Math.sin(angle) * 112} textAnchor="middle" dominantBaseline="middle" fill="#d4d4d8" fontSize="11">{LABELS[key]}</text>; })}
      <polygon points={polygon} fill="rgba(16,185,129,.25)" stroke="#34d399" strokeWidth="2" />
    </svg><div><p className="rounded-lg bg-zinc-900 p-3 text-xs leading-5 text-zinc-200">{report.star.suggestion}</p><h4 className="mt-3 text-xs font-semibold text-amber-200">建議優先審閱的 3 段</h4><ul className="mt-2 space-y-2">{report.lowest_confidence_segments.map((segment) => <li key={segment.segment_id} className="rounded-lg border border-zinc-800 p-2 text-xs"><div className="flex justify-between gap-2"><span>{segment.source_start.toFixed(1)}s–{segment.source_end.toFixed(1)}s · {segment.coaching_score}/100</span>{onJumpToSegment && <button onClick={() => onJumpToSegment(segment.source_start)} className="text-emerald-300">跳至片段</button>}</div><p className="mt-1 text-zinc-300">{segment.suggestions[0]}</p></li>)}</ul></div></div>
    <p className="mt-3 text-[10px] leading-4 text-zinc-500">{report.limitations[0]}</p>
  </section>;
}
