"use client";

import { useEffect, useState } from "react";

export interface FaceBounds {
  center: readonly [number, number];
  size: readonly [number, number];
  detected: boolean;
}

interface FaceMeshResults {
  multiFaceLandmarks?: Array<Array<{ x: number; y: number; z: number }>>;
}

interface FaceMeshInstance {
  setOptions(options: Record<string, unknown>): void;
  onResults(callback: (results: FaceMeshResults) => void): void;
  send(input: { image: HTMLVideoElement }): Promise<void>;
  close(): void;
}

declare global {
  interface Window {
    FaceMesh?: new (config: { locateFile: (name: string) => string }) => FaceMeshInstance;
  }
}

const CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh";
const fallback: FaceBounds = { center: [0.5, 0.5], size: [0.001, 0.001], detected: false };

function script(src: string): Promise<void> {
  if (document.querySelector(`script[src="${src}"]`)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const element = document.createElement("script");
    element.src = src; element.async = true; element.onload = () => resolve(); element.onerror = () => reject(new Error("MediaPipe Face Mesh 載入失敗"));
    document.head.appendChild(element);
  });
}

function bounds(landmarks: Array<{ x: number; y: number }>): FaceBounds {
  if (!landmarks.length) return fallback;
  const xValues = landmarks.map((item) => item.x); const yValues = landmarks.map((item) => item.y);
  const minX = Math.min(...xValues); const maxX = Math.max(...xValues); const minY = Math.min(...yValues); const maxY = Math.max(...yValues);
  const width = Math.min(1, (maxX - minX) * 1.28); const height = Math.min(1, (maxY - minY) * 1.35);
  return { center: [Math.max(width / 2, Math.min(1 - width / 2, (minX + maxX) / 2)), Math.max(height / 2, Math.min(1 - height / 2, (minY + maxY) / 2))], size: [width, height], detected: true };
}

/** Browser-side MediaPipe Face Mesh tracker. It sends only local video frames to the local WASM runtime. */
export function useFaceMesh(video: HTMLVideoElement | null, enabled = true): FaceBounds {
  const [face, setFace] = useState<FaceBounds>(fallback);
  useEffect(() => {
    if (!video || !enabled) { setFace(fallback); return; }
    let disposed = false; let pending = false; let mesh: FaceMeshInstance | null = null; let frame = 0;
    const start = async () => {
      try {
        await script(`${CDN}/face_mesh.js`);
        if (disposed || !window.FaceMesh) return;
        mesh = new window.FaceMesh({ locateFile: (name) => `${CDN}/${name}` });
        mesh.setOptions({ maxNumFaces: 1, refineLandmarks: true, minDetectionConfidence: .55, minTrackingConfidence: .55 });
        mesh.onResults((result) => { if (!disposed) setFace(bounds(result.multiFaceLandmarks?.[0] ?? [])); });
        const tick = async () => {
          if (disposed) return;
          if (!pending && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
            pending = true;
            try { await mesh?.send({ image: video }); } catch { if (!disposed) setFace(fallback); }
            pending = false;
          }
          frame = requestAnimationFrame(() => void tick());
        };
        void tick();
      } catch { if (!disposed) setFace(fallback); }
    };
    void start();
    return () => { disposed = true; cancelAnimationFrame(frame); mesh?.close(); };
  }, [video, enabled]);
  return face;
}
