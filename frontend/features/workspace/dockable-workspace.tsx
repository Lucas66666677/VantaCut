"use client";

import { createPortal } from "react-dom";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentProps } from "react";

import { createDockLayout, findPanel, insertPanel, pruneDockTree, removePanel, type DockEdge, type DockLayoutSnapshot, type DockNode, type FloatingDockPanel } from "@/features/workspace/dock-layout";
import { loadDockLayout, saveDockLayout } from "@/features/workspace/dock-layout-storage";
import { workspaceModuleRegistry } from "@/features/workspace/workspace-registry";
import { workspaceModuleLabels } from "@/features/workspace/workspace-store";
import type { TimelineClipInput } from "@/types/timeline";
import type { WorkspaceMode, WorkspaceModuleId } from "@/types/workspace";

type ModuleProps = ComponentProps<(typeof workspaceModuleRegistry)[WorkspaceModuleId]>;
type SlotMap = Partial<Record<WorkspaceModuleId, HTMLDivElement | null>>;
interface DragState { panelId: WorkspaceModuleId; pointerId: number; offsetX: number; offsetY: number; x: number; y: number; originX: number; originY: number; moved: boolean; candidate: { targetId: WorkspaceModuleId; edge: DockEdge } | null; }

const EDGES: DockEdge[] = ["left", "right", "top", "bottom"];

function edgeAtPoint(element: Element, clientX: number, clientY: number): DockEdge {
  const bounds = element.getBoundingClientRect();
  const distances: Record<DockEdge, number> = { left: clientX - bounds.left, right: bounds.right - clientX, top: clientY - bounds.top, bottom: bounds.bottom - clientY };
  return EDGES.reduce((closest, edge) => distances[edge] < distances[closest] ? edge : closest, "left");
}

function DockNodeView({ node, setSlot }: { node: DockNode; setSlot: (id: WorkspaceModuleId, slot: HTMLDivElement | null) => void }) {
  if (node.type === "panel") return <div ref={(element) => setSlot(node.panelId, element)} data-dock-slot={node.panelId} className="min-h-0 min-w-0 overflow-hidden" />;
  const first = `${Math.round(node.ratio * 100)}%`;
  const second = `${Math.round((1 - node.ratio) * 100)}%`;
  const style = node.direction === "row" ? { gridTemplateColumns: `${first} ${second}` } : { gridTemplateRows: `${first} ${second}` };
  return <div className="grid min-h-0 min-w-0 gap-1" style={style}><DockNodeView node={node.first} setSlot={setSlot} /><DockNodeView node={node.second} setSlot={setSlot} /></div>;
}

/**
 * A panel's React tree is portalled once into a stable host element. Docking only
 * reparents that host with appendChild, preventing Canvas/WebGL/video teardown.
 */
export function DockableWorkspace({ mode, enabledPanels, timeline, timelineId, projectId, userId }: { mode: WorkspaceMode; enabledPanels: WorkspaceModuleId[]; timeline: TimelineClipInput[]; timelineId?: string; projectId?: string; userId?: string }) {
  const storageKey = `workspace:${projectId ?? "local"}:${userId ?? "anonymous"}:${mode}`;
  const rootRef = useRef<HTMLDivElement>(null);
  const floatingLayerRef = useRef<HTMLDivElement>(null);
  const hostsRef = useRef(new Map<WorkspaceModuleId, HTMLDivElement>());
  const slotsRef = useRef<SlotMap>({});
  const dragRef = useRef<DragState | null>(null);
  const flipRectsRef = useRef(new Map<WorkspaceModuleId, DOMRect>());
  const [layout, setLayout] = useState<DockLayoutSnapshot>(() => createDockLayout(mode));
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [dragGuide, setDragGuide] = useState<DragState["candidate"]>(null);
  const enabled = useMemo(() => new Set(enabledPanels), [enabledPanels]);
  const visibleTree = useMemo(() => pruneDockTree(layout.root, enabled), [enabled, layout.root]);

  const getHost = useCallback((panelId: WorkspaceModuleId) => {
    let host = hostsRef.current.get(panelId);
    if (!host) {
      host = document.createElement("div");
      host.dataset.dockPanel = panelId;
      host.className = "h-full min-h-0 min-w-0 overflow-auto rounded-xl border border-zinc-800 bg-zinc-950 shadow-2xl";
      hostsRef.current.set(panelId, host);
    }
    return host;
  }, []);

  const captureFlip = useCallback(() => {
    flipRectsRef.current = new Map([...hostsRef.current.entries()].map(([id, host]) => [id, host.getBoundingClientRect()]));
  }, []);

  const commit = useCallback((next: (current: DockLayoutSnapshot) => DockLayoutSnapshot) => {
    captureFlip();
    setLayout((current) => next(current));
  }, [captureFlip]);

  useEffect(() => {
    let active = true;
    loadDockLayout(storageKey).then((saved) => { if (active) setLayout(saved?.version === 1 ? saved : createDockLayout(mode)); }).catch(() => { if (active) setLayout(createDockLayout(mode)); }).finally(() => { if (active) setLoadedKey(storageKey); });
    return () => { active = false; };
  }, [mode, storageKey]);

  useEffect(() => {
    if (loadedKey !== storageKey) return;
    const timer = window.setTimeout(() => { void saveDockLayout(storageKey, layout).catch(() => undefined); }, 350);
    return () => window.clearTimeout(timer);
  }, [layout, loadedKey, storageKey]);

  // FLIP: measure before state mutation, invert after DOM layout, then let WAAPI play.
  useLayoutEffect(() => {
    for (const [panelId, before] of flipRectsRef.current) {
      const host = hostsRef.current.get(panelId); if (!host || !host.isConnected) continue;
      const after = host.getBoundingClientRect();
      const dx = before.left - after.left; const dy = before.top - after.top;
      const sx = after.width ? before.width / after.width : 1; const sy = after.height ? before.height / after.height : 1;
      if (Math.abs(dx) + Math.abs(dy) + Math.abs(sx - 1) + Math.abs(sy - 1) < .5) continue;
      host.animate([{ transformOrigin: "top left", transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})` }, { transformOrigin: "top left", transform: "translate(0, 0) scale(1, 1)" }], { duration: 260, easing: "cubic-bezier(.2,.8,.2,1)" });
    }
    flipRectsRef.current.clear();
  }, [layout]);

  const placeHosts = useCallback(() => {
    const floatingIds = new Set(layout.floating.map((panel) => panel.panelId));
    enabledPanels.forEach((panelId) => {
      const host = getHost(panelId);
      const floating = layout.floating.find((panel) => panel.panelId === panelId);
      if (floating && floatingLayerRef.current) {
        host.style.cssText = `position:absolute;left:${floating.x}px;top:${floating.y}px;width:${floating.width}px;height:${floating.height}px;z-index:${floating.zIndex};`;
        if (host.parentElement !== floatingLayerRef.current) floatingLayerRef.current.appendChild(host);
        return;
      }
      if (!floatingIds.has(panelId) && slotsRef.current[panelId]) {
        host.removeAttribute("style");
        const slot = slotsRef.current[panelId]!;
        if (host.parentElement !== slot) slot.appendChild(host);
      }
    });
  }, [enabledPanels, getHost, layout.floating]);

  useLayoutEffect(() => { placeHosts(); }, [placeHosts, visibleTree]);

  const setSlot = useCallback((panelId: WorkspaceModuleId, element: HTMLDivElement | null) => { slotsRef.current[panelId] = element; }, []);

  const finishDrag = useCallback(() => {
    const drag = dragRef.current; if (!drag) return;
    const host = getHost(drag.panelId);
    const candidate = drag.candidate;
    dragRef.current = null; setDragGuide(null);
    if (!drag.moved) return;
    if (candidate && candidate.targetId !== drag.panelId) {
      commit((current) => {
        const without = removePanel(current.root, drag.panelId) ?? current.root;
        const root = findPanel(without, candidate.targetId) ? insertPanel(without, candidate.targetId, drag.panelId, candidate.edge) : without;
        return { ...current, root, floating: current.floating.filter((panel) => panel.panelId !== drag.panelId) };
      });
      return;
    }
    const rect = host.getBoundingClientRect();
    const layerBounds = floatingLayerRef.current?.getBoundingClientRect();
    const floating: FloatingDockPanel = { panelId: drag.panelId, x: Math.max(8, drag.x - (layerBounds?.left ?? 0) - drag.offsetX), y: Math.max(8, drag.y - (layerBounds?.top ?? 0) - drag.offsetY), width: Math.max(300, rect.width), height: Math.max(180, rect.height), zIndex: Date.now() };
    commit((current) => {
      const root = removePanel(current.root, drag.panelId) ?? current.root;
      return { ...current, root, floating: [...current.floating.filter((panel) => panel.panelId !== drag.panelId), floating] };
    });
  }, [commit, getHost]);

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const drag = dragRef.current; const layer = floatingLayerRef.current; if (!drag || drag.pointerId !== event.pointerId || !layer) return;
      drag.x = event.clientX; drag.y = event.clientY;
      if (!drag.moved && Math.hypot(event.clientX - drag.originX, event.clientY - drag.originY) < 4) return;
      drag.moved = true;
      const host = getHost(drag.panelId);
      const bounds = layer.getBoundingClientRect();
      const hostBounds = host.getBoundingClientRect();
      if (host.parentElement !== layer) layer.appendChild(host);
      host.style.cssText = `position:absolute;left:${event.clientX - bounds.left - drag.offsetX}px;top:${event.clientY - bounds.top - drag.offsetY}px;width:${Math.max(300, hostBounds.width)}px;height:${Math.max(180, hostBounds.height)}px;z-index:999;`;
      const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-dock-slot]");
      const candidate = target && target.dataset.dockSlot !== drag.panelId ? { targetId: target.dataset.dockSlot as WorkspaceModuleId, edge: edgeAtPoint(target, event.clientX, event.clientY) } : null;
      if (candidate?.targetId !== drag.candidate?.targetId || candidate?.edge !== drag.candidate?.edge) { drag.candidate = candidate; setDragGuide(candidate); }
    };
    const up = (event: PointerEvent) => { if (dragRef.current?.pointerId === event.pointerId) finishDrag(); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up); };
  }, [finishDrag, getHost]);

  const startDrag = useCallback((panelId: WorkspaceModuleId, event: React.PointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    const rect = getHost(panelId).getBoundingClientRect();
    dragRef.current = { panelId, pointerId: event.pointerId, offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top, x: event.clientX, y: event.clientY, originX: event.clientX, originY: event.clientY, moved: false, candidate: null };
  }, [getHost]);

  const moduleProps: ModuleProps = { timeline, timelineId, projectId, userId };
  const portals = enabledPanels.map((panelId) => {
    const Component = workspaceModuleRegistry[panelId]; const host = getHost(panelId);
    const isFloating = layout.floating.some((panel) => panel.panelId === panelId);
    return createPortal(<section className="flex h-full min-h-0 flex-col"><header className="flex shrink-0 touch-none items-center justify-between border-b border-zinc-800 bg-zinc-900/90 px-3 py-2"><button type="button" onPointerDown={(event) => startDrag(panelId, event)} className="cursor-grab text-xs font-semibold text-zinc-200 active:cursor-grabbing">⋮⋮ {workspaceModuleLabels[panelId]}</button><span className="text-[10px] text-zinc-500">{isFloating ? "浮動中" : "拖曳以停靠或撕下"}</span></header><div className="min-h-0 flex-1 overflow-auto p-2"><Component {...moduleProps} /></div></section>, host, `dock-portal-${panelId}`);
  });

  return <section ref={rootRef} className="relative mt-5 min-h-[680px] overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950"><div className="h-[680px] min-h-0 p-2">{visibleTree ? <DockNodeView node={visibleTree} setSlot={setSlot} /> : <div className="grid h-full place-items-center text-sm text-zinc-500">請從上方啟用至少一個面板。</div>}</div><div ref={floatingLayerRef} className="pointer-events-none absolute inset-0 z-40 [&>[data-dock-panel]]:pointer-events-auto" />{dragGuide && <div className="pointer-events-none absolute inset-2 z-30 rounded-xl border-2 border-cyan-300/80 bg-cyan-300/5" data-dock-guide={`${dragGuide.targetId}:${dragGuide.edge}`} />}{portals}</section>;
}
