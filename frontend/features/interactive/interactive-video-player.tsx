"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { InteractiveEdge, InteractiveManifest, InteractiveNode } from "@/types/interactive";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const CHOICE_WIDTH = 240;
const CHOICE_HEIGHT = 54;

export function InteractiveVideoPlayer({ timelineId }: { timelineId: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const enteredAt = useRef(0);
  const [manifest, setManifest] = useState<InteractiveManifest>();
  const [sessionId, setSessionId] = useState<string>();
  const [nodeId, setNodeId] = useState<string>();
  const [showChoices, setShowChoices] = useState(false);
  const [error, setError] = useState<string>();

  const nodes = useMemo(() => new Map(manifest?.nodes.map((node) => [node.id, node]) ?? []), [manifest]);
  const node = nodeId ? nodes.get(nodeId) : undefined;
  const choices = useMemo(() => manifest?.graph.edges.filter((edge) => edge.source_node_id === nodeId) ?? [], [manifest, nodeId]);

  const track = useCallback(async (event_type: "node_entered" | "choice_selected" | "session_ended", currentNodeId: string, edge?: InteractiveEdge) => {
    if (!sessionId) return;
    await fetch(`${API_URL}/api/v1/interactive/sessions/${sessionId}/events`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_type, node_id: currentNodeId, edge_id: edge?.id, watch_seconds: (performance.now() - enteredAt.current) / 1000 }),
    }).catch(() => undefined);
  }, [sessionId]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [manifestResponse, sessionResponse] = await Promise.all([
          fetch(`${API_URL}/api/v1/interactive/timelines/${timelineId}/manifest`),
          fetch(`${API_URL}/api/v1/interactive/timelines/${timelineId}/sessions`, { method: "POST" }),
        ]);
        if (!manifestResponse.ok || !sessionResponse.ok) throw new Error("互動影片暫時無法播放");
        const nextManifest = await manifestResponse.json() as InteractiveManifest;
        const session = await sessionResponse.json() as { session_id: string; entry_node_id: string };
        if (!alive) return;
        setManifest(nextManifest); setSessionId(session.session_id); setNodeId(session.entry_node_id);
      } catch (cause) { if (alive) setError(cause instanceof Error ? cause.message : "互動影片載入失敗"); }
    };
    void load();
    return () => { alive = false; };
  }, [timelineId]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !node || !sessionId) return;
    setShowChoices(false); enteredAt.current = performance.now();
    video.src = node.media_url; video.load();
    const start = () => { video.currentTime = node.source_start; void video.play().catch(() => undefined); void track("node_entered", node.id); };
    video.addEventListener("loadedmetadata", start, { once: true });
    return () => video.removeEventListener("loadedmetadata", start);
  }, [node, sessionId, track]);

  const drawChoices = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect(); const scale = window.devicePixelRatio || 1;
    canvas.width = rect.width * scale; canvas.height = rect.height * scale;
    const context = canvas.getContext("2d"); if (!context) return;
    context.scale(scale, scale); context.clearRect(0, 0, rect.width, rect.height);
    if (!showChoices) return;
    for (const choice of choices) {
      const x = choice.choice_position.x * rect.width - CHOICE_WIDTH / 2;
      const y = choice.choice_position.y * rect.height - CHOICE_HEIGHT / 2;
      context.fillStyle = "rgba(9, 18, 35, 0.88)"; context.strokeStyle = "#7dd3fc"; context.lineWidth = 1;
      context.beginPath(); context.roundRect(x, y, CHOICE_WIDTH, CHOICE_HEIGHT, 10); context.fill(); context.stroke();
      context.fillStyle = "#e0f2fe"; context.font = "600 14px sans-serif"; context.textAlign = "center"; context.textBaseline = "middle";
      context.fillText(choice.choice_text, x + CHOICE_WIDTH / 2, y + CHOICE_HEIGHT / 2, CHOICE_WIDTH - 24);
    }
  }, [choices, showChoices]);

  useEffect(() => { drawChoices(); window.addEventListener("resize", drawChoices); return () => window.removeEventListener("resize", drawChoices); }, [drawChoices]);

  const choose = useCallback((choice: InteractiveEdge) => {
    if (!node) return;
    void track("choice_selected", node.id, choice); setNodeId(choice.target_node_id);
  }, [node, track]);

  const handleCanvasClick = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!showChoices) return;
    const rect = event.currentTarget.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top;
    const choice = choices.find((edge) => Math.abs(x - edge.choice_position.x * rect.width) <= CHOICE_WIDTH / 2 && Math.abs(y - edge.choice_position.y * rect.height) <= CHOICE_HEIGHT / 2);
    if (choice) choose(choice);
  };

  if (error) return <p className="rounded-xl border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-100">{error}</p>;
  return <div className="relative aspect-video overflow-hidden rounded-2xl bg-black shadow-2xl">
    <video ref={videoRef} className="h-full w-full object-contain" playsInline preload="auto" onTimeUpdate={(event) => {
      if (node && event.currentTarget.currentTime >= node.source_end - 0.04) {
        event.currentTarget.pause(); if (choices.length) setShowChoices(true); else void track("session_ended", node.id);
      }
    }} />
    <canvas ref={canvasRef} onPointerDown={handleCanvasClick} className={`absolute inset-0 h-full w-full ${showChoices ? "cursor-pointer" : "pointer-events-none"}`} aria-label="互動選項" />
    {showChoices && <div className="pointer-events-none absolute bottom-4 left-0 right-0 text-center text-xs text-sky-100">請選擇下一步</div>}
    {choices.map((choice) => <video key={choice.id} className="hidden" preload="auto" src={nodes.get(choice.target_node_id)?.media_url} />)}
  </div>;
}
