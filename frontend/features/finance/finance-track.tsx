"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from "react";

type Point = { x: number; y: number };
type Annotation = { id: string; kind: "support" | "resistance"; p0: Point; p1: Point; p2: Point; p3: Point; label: string };
type Candle = { timestamp: string; open: number; high: number; low: number; close: number; volume: number; indicators?: Record<string, number | null> };
type FinanceTrackState = {
  id: string; status: "processing" | "completed" | "failed"; symbol: string; market: string;
  candles?: Candle[]; annotations?: Annotation[]; data_notice?: string; error?: string;
};

const formatDate = (value: Date) => value.toISOString().slice(0, 10);

function cubic(p0: Point, p1: Point, p2: Point, p3: Point, t: number): Point {
  const reverse = 1 - t;
  return {
    x: reverse ** 3 * p0.x + 3 * reverse ** 2 * t * p1.x + 3 * reverse * t ** 2 * p2.x + t ** 3 * p3.x,
    y: reverse ** 3 * p0.y + 3 * reverse ** 2 * t * p1.y + 3 * reverse * t ** 2 * p2.y + t ** 3 * p3.y,
  };
}

function drawChart(canvas: HTMLCanvasElement, candles: Candle[], annotations: Annotation[], progress: number) {
  const context = canvas.getContext("2d");
  if (!context) return;
  const { width, height } = canvas;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#09090b"; context.fillRect(0, 0, width, height);
  const visible = candles.slice(0, Math.max(1, Math.ceil(candles.length * progress)));
  const values = visible.flatMap((item) => [item.low, item.high]);
  const min = Math.min(...values), max = Math.max(...values), range = Math.max(max - min, 0.001);
  const left = 42, right = width - 14, top = 20, bottom = height - 28;
  const y = (value: number) => bottom - ((value - min) / range) * (bottom - top);
  context.strokeStyle = "rgba(161,161,170,.25)"; context.lineWidth = 1;
  for (let index = 0; index < 5; index += 1) { const row = top + (bottom - top) * index / 4; context.beginPath(); context.moveTo(left, row); context.lineTo(right, row); context.stroke(); }
  const step = (right - left) / Math.max(visible.length, 1), body = Math.max(2, step * .58);
  visible.forEach((item, index) => {
    const x = left + step * (index + .5), up = item.close >= item.open, color = up ? "#34d399" : "#fb7185";
    context.strokeStyle = color; context.fillStyle = color; context.beginPath(); context.moveTo(x, y(item.high)); context.lineTo(x, y(item.low)); context.stroke();
    const bodyTop = y(Math.max(item.open, item.close)), bodyHeight = Math.max(1.5, Math.abs(y(item.open) - y(item.close)));
    context.fillRect(x - body / 2, bodyTop, body, bodyHeight);
  });
  annotations.forEach((line) => {
    context.strokeStyle = line.kind === "support" ? "#60a5fa" : "#fbbf24"; context.lineWidth = 2; context.setLineDash([6, 4]); context.beginPath();
    for (let index = 0; index <= 40; index += 1) { const p = cubic(line.p0, line.p1, line.p2, line.p3, index / 40); const px = p.x * width, py = p.y * height; if (index === 0) context.moveTo(px, py); else context.lineTo(px, py); }
    context.stroke(); context.setLineDash([]); context.fillStyle = line.kind === "support" ? "#bfdbfe" : "#fde68a"; context.font = "12px sans-serif"; context.fillText(line.label, line.p3.x * width + 6, line.p3.y * height - 6);
  });
  context.fillStyle = "#d4d4d8"; context.font = "12px sans-serif"; context.fillText(`最高 ${max.toFixed(2)}`, 4, top + 4); context.fillText(`最低 ${min.toFixed(2)}`, 4, bottom);
}

export function FinanceTrack({ timelineId, userId, apiBase = "/api/v1" }: { timelineId?: string; userId?: string; apiBase?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null); const animationRef = useRef<number | null>(null);
  const now = useMemo(() => new Date(), []); const [symbol, setSymbol] = useState("2353"); const [market, setMarket] = useState("twse");
  const [historyStart, setHistoryStart] = useState(formatDate(new Date(now.getTime() - 180 * 86400000))); const [historyEnd, setHistoryEnd] = useState(formatDate(now));
  const [timelineStart, setTimelineStart] = useState("0"); const [timelineEnd, setTimelineEnd] = useState("10"); const [kind, setKind] = useState<Annotation["kind"]>("support");
  const [annotations, setAnnotations] = useState<Annotation[]>([]); const [drawing, setDrawing] = useState<Point | null>(null); const [tracks, setTracks] = useState<FinanceTrackState[]>([]); const [message, setMessage] = useState("");
  const active = tracks.at(-1); const candles = active?.candles ?? [];

  const loadTracks = useCallback(async () => {
    if (!timelineId || !userId) return;
    const response = await fetch(`${apiBase}/timelines/${timelineId}/finance-tracks?user_id=${encodeURIComponent(userId)}`);
    if (!response.ok) throw new Error(await response.text());
    const payload = await response.json() as { tracks: FinanceTrackState[] };
    setTracks(payload.tracks); const latest = payload.tracks.at(-1); if (latest?.annotations) setAnnotations(latest.annotations);
  }, [apiBase, timelineId, userId]);

  useEffect(() => { void loadTracks().catch((error: unknown) => setMessage(`讀取金融軌道失敗：${error instanceof Error ? error.message : "未知錯誤"}`)); }, [loadTracks]);
  useEffect(() => {
    if (active?.status !== "processing") return;
    const timer = window.setInterval(() => void loadTracks().catch(() => undefined), 2500);
    return () => window.clearInterval(timer);
  }, [active?.status, loadTracks]);
  useEffect(() => {
    if (!canvasRef.current || !candles.length) return;
    const start = performance.now(); const render = (time: number) => { drawChart(canvasRef.current!, candles, annotations, Math.min(1, (time - start) / 900)); if (time - start < 900) animationRef.current = requestAnimationFrame(render); };
    animationRef.current = requestAnimationFrame(render); return () => { if (animationRef.current) cancelAnimationFrame(animationRef.current); };
  }, [annotations, candles]);

  const createTrack = async () => {
    if (!timelineId || !userId) { setMessage("需要 timelineId 與 userId 才能建立金融軌道。"); return; }
    setMessage("正在排入市場資料與 K 線渲染工作…");
    const response = await fetch(`${apiBase}/timelines/${timelineId}/finance-tracks`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, symbol, market, history_start: historyStart, history_end: historyEnd, start_time: Number(timelineStart), end_time: Number(timelineEnd), annotations }) });
    if (!response.ok) { setMessage(`建立失敗：${await response.text()}`); return; }
    setMessage("金融軌道已排入工作佇列。"); await loadTracks();
  };
  const finishLine = async (end: Point) => {
    if (!drawing) return; const next: Annotation = { id: crypto.randomUUID(), kind, p0: drawing, p1: { x: drawing.x + (end.x - drawing.x) / 3, y: drawing.y }, p2: { x: drawing.x + 2 * (end.x - drawing.x) / 3, y: end.y }, p3: end, label: kind === "support" ? "Support" : "Resistance" };
    const updated = [...annotations, next]; setAnnotations(updated); setDrawing(null);
    if (!timelineId || !userId || !active) return;
    const response = await fetch(`${apiBase}/timelines/${timelineId}/finance-tracks/${active.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, annotations: updated }) });
    setMessage(response.ok ? "支撐／壓力貝茲線已重新排入合成。" : `標記儲存失敗：${await response.text()}`); if (response.ok) await loadTracks();
  };
  const normalisedPoint = (event: PointerEvent<HTMLCanvasElement>): Point => { const rect = event.currentTarget.getBoundingClientRect(); return { x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)), y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)) }; };

  return <aside className="rounded-xl border border-emerald-500/25 bg-emerald-950/15 p-3 text-zinc-100">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-sm font-semibold">金融軌道</h3><p className="text-xs text-zinc-400">K 線與技術指標會輸出成透明圖層；支撐／壓力線將隨圖表一起合成。</p></div><span className="rounded bg-amber-400/10 px-2 py-1 text-[10px] text-amber-200">僅供教學視覺化，非投資建議</span></div>
    <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6"><input value={symbol} onChange={(event) => setSymbol(event.target.value)} aria-label="股票代號" placeholder="2353" className="rounded bg-zinc-950 p-2 text-xs ring-1 ring-zinc-700" /><select value={market} onChange={(event) => setMarket(event.target.value)} className="rounded bg-zinc-950 p-2 text-xs ring-1 ring-zinc-700"><option value="twse">TWSE（日資料）</option><option value="yahoo_compatible">授權相容供應商</option></select><input type="date" value={historyStart} onChange={(event) => setHistoryStart(event.target.value)} className="rounded bg-zinc-950 p-2 text-xs ring-1 ring-zinc-700" /><input type="date" value={historyEnd} onChange={(event) => setHistoryEnd(event.target.value)} className="rounded bg-zinc-950 p-2 text-xs ring-1 ring-zinc-700" /><input type="number" min="0" value={timelineStart} onChange={(event) => setTimelineStart(event.target.value)} aria-label="圖表開始秒數" className="rounded bg-zinc-950 p-2 text-xs ring-1 ring-zinc-700" /><input type="number" min="1" value={timelineEnd} onChange={(event) => setTimelineEnd(event.target.value)} aria-label="圖表結束秒數" className="rounded bg-zinc-950 p-2 text-xs ring-1 ring-zinc-700" /></div>
    <div className="mt-2 flex items-center gap-2"><button onClick={() => void createTrack()} className="rounded bg-emerald-500 px-3 py-2 text-xs font-semibold text-emerald-950">建立金融軌道</button><select value={kind} onChange={(event) => setKind(event.target.value as Annotation["kind"])} className="rounded bg-zinc-950 p-2 text-xs ring-1 ring-zinc-700"><option value="support">繪製支撐線</option><option value="resistance">繪製壓力線</option></select><span className="text-xs text-zinc-400">{active?.status === "processing" ? "資料與動畫生成中…" : message}</span></div>
    <canvas ref={canvasRef} width={760} height={300} onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); setDrawing(normalisedPoint(event)); }} onPointerUp={(event) => void finishLine(normalisedPoint(event))} className="mt-3 h-auto w-full touch-none rounded border border-zinc-700 bg-zinc-950" aria-label="可繪製支撐線與壓力線的 K 線圖" />
    {active?.data_notice && <p className="mt-2 text-[11px] text-zinc-400">{active.data_notice}</p>}{active?.status === "failed" && <p className="mt-2 text-xs text-red-300">金融軌道失敗：{active.error}</p>}
  </aside>;
}
