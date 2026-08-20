"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { authenticatedFetch } from "@/lib/api/authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface Pairing {
  pairing_id: string;
  session_id: string;
  label: string;
  camera_index: number;
  mobile_url: string;
  qr_code_data_uri: string;
  server_epoch_ms: number;
  capture_origin_ms: number;
}

interface SignalMessage {
  type: "offer" | "answer" | "candidate" | "hangup" | "presence";
  sdp?: string;
  candidate?: RTCIceCandidateInit;
  mobile_connected?: boolean;
}

function websocketUrl(pairing: Pairing): string {
  const url = new URL(API_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `/api/v1/wireless-cameras/pairings/${pairing.pairing_id}/signal`;
  url.searchParams.set("token", new URL(pairing.mobile_url).searchParams.get("token") ?? "");
  url.searchParams.set("role", "editor");
  return url.toString();
}

function CameraCanvas({ stream, label }: { stream: MediaStream | null; label: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.srcObject = stream;
    if (stream) void video.play().catch(() => undefined);
  }, [stream]);

  useEffect(() => {
    let frame = 0;
    const render = () => {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (video && canvas && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
        const width = video.videoWidth || 640;
        const height = video.videoHeight || 360;
        if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
        canvas.getContext("2d")?.drawImage(video, 0, 0, width, height);
      }
      frame = requestAnimationFrame(render);
    };
    frame = requestAnimationFrame(render);
    return () => cancelAnimationFrame(frame);
  }, []);

  return <div className="overflow-hidden rounded-lg border border-cyan-300/30 bg-zinc-950">
    <video ref={videoRef} muted playsInline className="hidden" />
    <canvas ref={canvasRef} className="aspect-video w-full bg-zinc-900 object-contain" />
    <p className="border-t border-zinc-800 px-2 py-1 text-[11px] text-cyan-100">{label} · WebRTC 即時畫布</p>
  </div>;
}

export function WirelessCameraPanel({ timelineId, projectId, userId }: { timelineId?: string; projectId?: string; userId?: string }) {
  const [pairings, setPairings] = useState<Pairing[]>([]);
  const [streams, setStreams] = useState<Record<string, MediaStream | null>>({});
  const [mobileConnected, setMobileConnected] = useState<Record<string, boolean>>({});
  const [label, setLabel] = useState("無線鏡頭");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const createPairing = useCallback(async () => {
    if (!timelineId || !userId || pairings.length >= 2) return;
    setCreating(true); setError(null);
    try {
      const response = await authenticatedFetch(`${API_URL}/api/v1/timelines/${timelineId}/wireless-cameras/pairings`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: label || `無線鏡頭 ${pairings.length + 1}` }),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail ?? "無法建立手機配對");
      const pairing = await response.json() as Pairing;
      setPairings((current) => [...current, pairing]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法建立手機配對");
    } finally { setCreating(false); }
  }, [label, pairings.length, timelineId, userId]);

  useEffect(() => {
    const cleanups = pairings.map((pairing) => {
      const peer = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
      const socket = new WebSocket(websocketUrl(pairing));
      peer.ontrack = (event) => setStreams((current) => ({ ...current, [pairing.pairing_id]: event.streams[0] ?? new MediaStream([event.track]) }));
      peer.onicecandidate = (event) => {
        if (event.candidate && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "candidate", candidate: event.candidate.toJSON() }));
      };
      socket.onmessage = async (event) => {
        const message = JSON.parse(event.data) as SignalMessage;
        if (message.type === "presence") { setMobileConnected((current) => ({ ...current, [pairing.pairing_id]: Boolean(message.mobile_connected) })); return; }
        if (message.type === "offer" && message.sdp) {
          await peer.setRemoteDescription({ type: "offer", sdp: message.sdp });
          const answer = await peer.createAnswer(); await peer.setLocalDescription(answer);
          socket.send(JSON.stringify({ type: "answer", sdp: answer.sdp }));
        } else if (message.type === "candidate" && message.candidate) await peer.addIceCandidate(message.candidate);
      };
      return () => { socket.close(); peer.close(); };
    });
    return () => cleanups.forEach((cleanup) => cleanup());
  }, [pairings]);

  if (!timelineId || !projectId || !userId) return null;
  return <div className="mb-3 rounded-xl border border-cyan-400/30 bg-cyan-500/5 p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div><p className="text-sm font-medium text-cyan-100">跨裝置無線多機位</p><p className="text-[11px] text-zinc-400">掃 QR 後，手機直接成為無線鏡頭；兩路錄製以伺服器時鐘自動對齊。</p></div>
      <div className="flex gap-2"><input value={label} onChange={(event) => setLabel(event.target.value)} className="w-28 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs" placeholder="鏡頭名稱" />
        <button disabled={creating || pairings.length >= 2} onClick={() => void createPairing()} className="rounded border border-cyan-300/70 bg-cyan-400/15 px-3 py-1.5 text-xs text-cyan-50 disabled:opacity-40">{creating ? "建立中…" : `新增無線鏡頭 (${pairings.length}/2)`}</button></div>
    </div>
    {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
    {pairings.length > 0 && <div className="mt-3 grid gap-3 md:grid-cols-2">
      {pairings.map((pairing) => <div key={pairing.pairing_id} className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-2">
        <div className="flex gap-3"><img src={pairing.qr_code_data_uri} alt={`${pairing.label} 手機配對 QR Code`} className="h-20 w-20 rounded bg-white p-1" />
          <div className="text-xs"><p className="font-medium text-zinc-100">鏡頭 {pairing.camera_index} · {pairing.label}</p><p className={mobileConnected[pairing.pairing_id] ? "mt-1 text-emerald-300" : "mt-1 text-amber-300"}>{mobileConnected[pairing.pairing_id] ? "手機已連線，等待／正在推流" : "掃碼後等待手機連線"}</p><p className="mt-1 text-zinc-500">同步原點：{new Date(pairing.capture_origin_ms).toLocaleTimeString()}</p></div>
        </div>
        <div className="mt-2"><CameraCanvas stream={streams[pairing.pairing_id] ?? null} label={pairing.label} /></div>
      </div>)}
    </div>}
  </div>;
}
