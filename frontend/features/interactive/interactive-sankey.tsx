"use client";

import type { InteractiveAnalytics } from "@/types/interactive";

export function InteractiveSankey({ analytics }: { analytics: InteractiveAnalytics }) {
  const width = 760; const height = Math.max(220, analytics.nodes.length * 78);
  const positions = new Map(analytics.nodes.map((node, index) => [node.id, { x: index === 0 ? 30 : width - 170, y: 30 + index * 72 }]));
  return <section className="rounded-2xl border border-zinc-800 bg-zinc-950 p-4"><div className="mb-3 flex justify-between text-sm"><h3 className="font-semibold">觀眾分支路徑</h3><span className="text-zinc-400">{analytics.sessions} sessions</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full overflow-visible" role="img" aria-label="互動分支桑基圖">
      {analytics.links.map((link) => { const source = positions.get(link.source); const target = positions.get(link.target); if (!source || !target) return null; return <path key={link.edge_id} d={`M${source.x + 140},${source.y + 20} C${width / 2},${source.y + 20} ${width / 2},${target.y + 20} ${target.x},${target.y + 20}`} fill="none" stroke="#38bdf8" strokeOpacity=".35" strokeWidth={Math.max(2, link.value * 3)}><title>{`${link.label}: ${link.choice_share_percent}% (${link.value})`}</title></path>; })}
      {analytics.nodes.map((node) => { const point = positions.get(node.id)!; return <g key={node.id}><rect x={point.x} y={point.y} width="140" height="42" rx="8" fill="#18181b" stroke="#52525b"/><text x={point.x + 10} y={point.y + 17} fill="#f4f4f5" fontSize="11">{node.label.slice(0, 18)}</text><text x={point.x + 10} y={point.y + 32} fill="#a1a1aa" fontSize="9">{node.visits} 次 · {node.average_dwell_seconds.toFixed(1)}s</text></g>; })}
    </svg>
  </section>;
}
