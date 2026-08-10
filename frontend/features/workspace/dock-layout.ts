import type { WorkspaceMode, WorkspaceModuleId } from "@/types/workspace";

export type DockEdge = "left" | "right" | "top" | "bottom";

export interface DockLeaf {
  type: "panel";
  panelId: WorkspaceModuleId;
}

export interface DockSplit {
  type: "split";
  direction: "row" | "column";
  ratio: number;
  first: DockNode;
  second: DockNode;
}

export type DockNode = DockLeaf | DockSplit;

export interface FloatingDockPanel {
  panelId: WorkspaceModuleId;
  x: number;
  y: number;
  width: number;
  height: number;
  zIndex: number;
}

export interface DockLayoutSnapshot {
  version: 1;
  mode: WorkspaceMode;
  root: DockNode;
  floating: FloatingDockPanel[];
}

const leaf = (panelId: WorkspaceModuleId): DockLeaf => ({ type: "panel", panelId });
const split = (direction: DockSplit["direction"], first: DockNode, second: DockNode, ratio = .7): DockSplit => ({ type: "split", direction, first, second, ratio });

export function createDockLayout(mode: WorkspaceMode): DockLayoutSnapshot {
  const editing = split("column", split("row", leaf("timeline"), split("column", leaf("inspector"), leaf("color_wheels"), .5), .72), split("row", leaf("scopes"), leaf("audio_mixer"), .5), .72);
  const color = split("row", split("column", leaf("timeline"), leaf("scopes"), .65), split("column", leaf("color_wheels"), leaf("inspector"), .68), .62);
  const audio = split("column", split("row", leaf("timeline"), leaf("inspector"), .74), split("row", leaf("audio_mixer"), leaf("scopes"), .62), .6);
  return { version: 1, mode, root: mode === "color" ? color : mode === "audio" ? audio : editing, floating: [] };
}

export function cloneDockLayout(layout: DockLayoutSnapshot): DockLayoutSnapshot {
  return structuredClone(layout);
}

export function findPanel(node: DockNode, panelId: WorkspaceModuleId): boolean {
  return node.type === "panel" ? node.panelId === panelId : findPanel(node.first, panelId) || findPanel(node.second, panelId);
}

/** Removes a panel while collapsing its now-unnecessary branch. */
export function removePanel(node: DockNode, panelId: WorkspaceModuleId): DockNode | null {
  if (node.type === "panel") return node.panelId === panelId ? null : node;
  const first = removePanel(node.first, panelId);
  const second = removePanel(node.second, panelId);
  if (!first) return second;
  if (!second) return first;
  return { ...node, first, second };
}

export function insertPanel(node: DockNode, targetId: WorkspaceModuleId, panelId: WorkspaceModuleId, edge: DockEdge): DockNode {
  if (node.type === "panel") {
    if (node.panelId !== targetId) return node;
    const direction = edge === "left" || edge === "right" ? "row" : "column";
    const inserted = leaf(panelId);
    return edge === "left" || edge === "top" ? split(direction, inserted, node) : split(direction, node, inserted);
  }
  return { ...node, first: insertPanel(node.first, targetId, panelId, edge), second: insertPanel(node.second, targetId, panelId, edge) };
}

/** Removes disabled leaves without changing the saved tree. */
export function pruneDockTree(node: DockNode, visible: Set<WorkspaceModuleId>): DockNode | null {
  if (node.type === "panel") return visible.has(node.panelId) ? node : null;
  const first = pruneDockTree(node.first, visible);
  const second = pruneDockTree(node.second, visible);
  if (!first) return second;
  if (!second) return first;
  return { ...node, first, second };
}

