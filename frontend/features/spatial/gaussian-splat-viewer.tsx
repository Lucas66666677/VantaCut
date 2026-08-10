"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Vector3 } from "three";

import { requestVirtualCameraRender } from "@/lib/spatial/spatial-api";
import type { SpatialSceneManifest, VirtualCameraKeyframe } from "@/types/spatial";

interface GaussianSplatViewerProps {
  mediaAssetId: string;
  userId: string;
  scene: SpatialSceneManifest;
}

type ViewerLike = {
  camera?: { position: { x: number; y: number; z: number }; fov?: number; getWorldDirection: (target: Vector3) => Vector3 };
  addSplatScene: (url: string, options?: Record<string, unknown>) => Promise<void>;
  start: () => void;
  dispose?: () => void;
};

export function GaussianSplatViewer({ mediaAssetId, userId, scene }: GaussianSplatViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<ViewerLike | null>(null);
  const [path, setPath] = useState<VirtualCameraKeyframe[]>([]);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const splatUrl = scene.splat_url;
    if (!splatUrl || !containerRef.current) return;
    let disposed = false;
    (async () => {
      try {
        const module = await import("@mkkellogg/gaussian-splats-3d") as unknown as { Viewer: new (options: Record<string, unknown>) => ViewerLike };
        if (disposed || !containerRef.current) return;
        const viewer = new module.Viewer({ rootElement: containerRef.current, cameraUp: [0, -1, -0.6], initialCameraPosition: [0, -1, 4], initialCameraLookAt: [0, 0, 0], gpuAcceleratedSort: true, freeIntermediateSplatData: true });
        await viewer.addSplatScene(splatUrl, { progressiveLoad: true, showLoadingUI: false, splatAlphaRemovalThreshold: 5 });
        if (disposed) { viewer.dispose?.(); return; }
        viewer.start(); viewerRef.current = viewer;
      } catch (viewerError) { setError(viewerError instanceof Error ? viewerError.message : "Unable to load the Gaussian Splat scene."); }
    })();
    return () => { disposed = true; viewerRef.current?.dispose?.(); viewerRef.current = null; };
  }, [scene.splat_url]);

  const addKeyframe = useCallback(() => {
    const camera = viewerRef.current?.camera;
    if (!camera) return;
    const direction = camera.getWorldDirection(new Vector3());
    const position: [number, number, number] = [camera.position.x, camera.position.y, camera.position.z];
    setPath((current) => [...current, { time_seconds: current.length ? current.at(-1)!.time_seconds + 2 : 0, position, look_at: [position[0] + direction.x, position[1] + direction.y, position[2] + direction.z], fov_degrees: camera.fov ?? 55 }]);
  }, []);

  const renderPath = useCallback(async () => {
    if (path.length < 2) { setError("至少設定兩個虛擬攝影機關鍵幀。"); return; }
    setRendering(true); setError(null);
    try { await requestVirtualCameraRender(mediaAssetId, userId, path, { fps: 30, width: 1920, height: 1080 }); }
    catch (renderError) { setError(renderError instanceof Error ? renderError.message : "Virtual camera render could not be queued."); }
    finally { setRendering(false); }
  }, [mediaAssetId, path, userId]);

  return (
    <section className="grid gap-3 rounded-2xl border border-zinc-800 bg-zinc-950 p-3 lg:grid-cols-[minmax(0,1fr)_260px]">
      <div ref={containerRef} className="min-h-[420px] overflow-hidden rounded-xl bg-black" />
      <aside className="space-y-3 text-xs text-zinc-300">
        <div><h3 className="font-semibold text-zinc-100">3D Gaussian Splat 虛擬攝影機</h3><p className="mt-1 text-zinc-500">已註冊 {scene.registered_pose_count ?? 0} 個拍攝姿態。只在已觀測視差附近運鏡，以降低遮擋空洞。</p></div>
        <button onClick={addKeyframe} className="w-full rounded bg-sky-400 px-3 py-2 font-medium text-slate-950">以目前視角加入關鍵幀</button>
        <ol className="max-h-48 space-y-1 overflow-auto rounded border border-zinc-800 p-2">{path.map((keyframe, index) => <li key={`${keyframe.time_seconds}-${index}`}>#{index + 1} · {keyframe.time_seconds.toFixed(1)}s · ({keyframe.position.map((value) => value.toFixed(2)).join(", ")})</li>)}</ol>
        <button disabled={rendering || path.length < 2} onClick={renderPath} className="w-full rounded bg-violet-400 px-3 py-2 font-medium text-violet-950 disabled:opacity-40">{rendering ? "正在排入 GPU 導出…" : "導出新運鏡影片"}</button>
        {error && <p className="text-amber-300">{error}</p>}
      </aside>
    </section>
  );
}
