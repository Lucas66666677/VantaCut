"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const CHUNK_MS = 2_000;

function wsUrl(pairingId: string, token: string): string {
  const url = new URL(API_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `/api/v1/wireless-cameras/pairings/${pairingId}/signal`;
  url.searchParams.set("token", token); url.searchParams.set("role", "mobile");
  return url.toString();
}

export default function WirelessCameraPage() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const sequenceRef = useRef(0);
  const uploadsRef = useRef<Promise<void>>(Promise.resolve());
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [message, setMessage] = useState("正在等待編輯器…");
  const [recording, setRecording] = useState(false);
  const [ready, setReady] = useState(false);
  const [clockSkewMs, setClockSkewMs] = useState(0);
  const query = useMemo(() => new URLSearchParams(typeof window === "undefined" ? "" : window.location.search), []);
  const pairingId = query.get("pairing") ?? "";
  const token = query.get("token") ?? "";

  useEffect(() => {
    if (!pairingId || !token) { setMessage("配對連結無效或已過期。"); return; }
    let cancelled = false;
    let localStream: MediaStream | null = null;
    const connect = async () => {
      try {
        const clockResponse = await fetch(`${API_URL}/api/v1/wireless-cameras/pairings/${pairingId}/clock`, { headers: { "X-Wireless-Camera-Token": token } });
        if (clockResponse.ok) {
          const clock = await clockResponse.json() as { server_epoch_ms: number };
          setClockSkewMs(clock.server_epoch_ms - Date.now());
        }
        const local = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: true });
        localStream = local;
        if (cancelled) { local.getTracks().forEach((track) => track.stop()); return; }
        setStream(local); if (videoRef.current) { videoRef.current.srcObject = local; await videoRef.current.play(); }
        const peer = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] }); peerRef.current = peer;
        local.getTracks().forEach((track) => peer.addTrack(track, local));
        const socket = new WebSocket(wsUrl(pairingId, token)); socketRef.current = socket;
        peer.onicecandidate = (event) => { if (event.candidate && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "candidate", candidate: event.candidate.toJSON() })); };
        let offerSent = false;
        const sendOffer = async () => {
          if (offerSent || socket.readyState !== WebSocket.OPEN) return;
          offerSent = true;
          const offer = await peer.createOffer(); await peer.setLocalDescription(offer);
          socket.send(JSON.stringify({ type: "offer", sdp: offer.sdp }));
        };
        socket.onmessage = async (event) => {
          const data = JSON.parse(event.data) as { type: string; sdp?: string; candidate?: RTCIceCandidateInit; editor_connected?: boolean };
          if (data.type === "presence") { setReady(Boolean(data.editor_connected)); setMessage(data.editor_connected ? "已連到編輯器，可以開始錄影。" : "已開啟相機，等待編輯器…"); if (data.editor_connected) await sendOffer(); }
          if (data.type === "answer" && data.sdp) await peer.setRemoteDescription({ type: "answer", sdp: data.sdp });
          if (data.type === "candidate" && data.candidate) await peer.addIceCandidate(data.candidate);
        };
        socket.onopen = () => undefined;
      } catch (error) { setMessage(error instanceof Error ? `無法啟動鏡頭：${error.message}` : "無法啟動鏡頭"); }
    };
    void connect();
    return () => { cancelled = true; recorderRef.current?.stop(); socketRef.current?.close(); peerRef.current?.close(); localStream?.getTracks().forEach((track) => track.stop()); };
    // This lifecycle owns the acquired stream and only reconnects for a new pairing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairingId, token]);

  const start = async () => {
    if (!stream) return;
    const serverAlignedStartedAt = Date.now() + clockSkewMs;
    const response = await fetch(`${API_URL}/api/v1/wireless-cameras/pairings/${pairingId}/start`, { method: "POST", headers: { "Content-Type": "application/json", "X-Wireless-Camera-Token": token }, body: JSON.stringify({ server_aligned_started_at_ms: serverAlignedStartedAt }) });
    if (!response.ok) { setMessage("無法開始雲端錄製，請重新掃描 QR Code。"); return; }
    sequenceRef.current = 0;
    const mimeType = MediaRecorder.isTypeSupported("video/webm;codecs=vp9,opus") ? "video/webm;codecs=vp9,opus" : "video/webm";
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 2_500_000 }); recorderRef.current = recorder;
    recorder.ondataavailable = (event) => {
      if (!event.data.size) return;
      const sequence = sequenceRef.current++;
      uploadsRef.current = uploadsRef.current.then(async () => {
        const upload = await fetch(`${API_URL}/api/v1/wireless-cameras/pairings/${pairingId}/chunks/${sequence}`, { method: "PUT", headers: { "Content-Type": event.data.type || "video/webm", "X-Wireless-Camera-Token": token }, body: event.data });
        if (!upload.ok) throw new Error("片段上傳失敗");
      }).catch(() => setMessage("有一段影片未成功上傳，請保持網路連線後重試。"));
    };
    recorder.start(CHUNK_MS); setRecording(true); setMessage("正在錄影並即時寫入時間軸…");
  };

  const stop = async () => {
    const recorder = recorderRef.current;
    setRecording(false); setMessage("正在送出最後片段並對齊時間軸…");
    if (recorder && recorder.state !== "inactive") {
      await new Promise<void>((resolve) => {
        recorder.addEventListener("stop", () => resolve(), { once: true });
        recorder.stop();
      });
    }
    await uploadsRef.current;
    await fetch(`${API_URL}/api/v1/wireless-cameras/pairings/${pairingId}/complete`, { method: "POST", headers: { "X-Wireless-Camera-Token": token } });
    setMessage("錄影完成，代理檔將自動出現在編輯器的多機位軌道。\n");
  };

  return <main className="mx-auto flex min-h-screen max-w-lg flex-col gap-4 bg-zinc-950 p-4 text-zinc-100"><h1 className="text-lg font-semibold">無線鏡頭</h1><p className="text-sm text-zinc-400">{message}</p><video ref={videoRef} muted playsInline className="aspect-video w-full rounded-xl bg-zinc-900 object-cover" />
    <button disabled={!stream || !ready} onClick={() => void (recording ? stop() : start())} className={`rounded-xl px-4 py-4 font-semibold disabled:opacity-40 ${recording ? "bg-red-500 text-white" : "bg-cyan-400 text-zinc-950"}`}>{recording ? "停止錄影" : "開始錄影"}</button>
    <p className="text-center text-xs text-zinc-500">即時預覽走 WebRTC；錄影會每 2 秒安全上傳為可即時剪輯的片段。</p></main>;
}
