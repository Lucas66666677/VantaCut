"use client";

import type { ClipLayout } from "@/types/timeline";
import type { CollaboratorPresence } from "@/features/editor/use-collaborative-timeline";

interface CollaborationPresenceOverlayProps {
  peers: CollaboratorPresence[];
  layouts: ClipLayout[];
  zoom: number;
}

export function CollaborationPresenceOverlay({ peers, layouts, zoom }: CollaborationPresenceOverlayProps) {
  return (
    <>
      {peers.map((peer) => {
        const selected = layouts.find((clip) => clip.id === (peer.lockedClipId ?? peer.selectedClipId));
        return (
          <div key={peer.id} className="pointer-events-none absolute inset-0 z-50">
            <div className="absolute bottom-0 top-0 w-px" style={{ left: peer.playheadTime * zoom, backgroundColor: peer.color }}>
              <span className="absolute -left-2 top-1 whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] text-white" style={{ backgroundColor: peer.color }}>{peer.name}</span>
            </div>
            {peer.cursor?.surface === "timeline" && <div className="absolute z-[60] -translate-x-0.5 -translate-y-0.5 transition-transform duration-75" style={{ left: `${peer.cursor.x * 100}%`, top: `${peer.cursor.y * 100}%` }}><span className="block text-lg leading-none drop-shadow">➤</span><span className="ml-2 block w-max rounded px-1.5 py-0.5 text-[10px] font-semibold text-white shadow" style={{ backgroundColor: peer.color }}>{peer.name}</span></div>}
            {selected && (
              <div
                className="absolute top-[35px] h-9 rounded border-2"
                style={{
                  left: selected.displayStart * zoom,
                  width: Math.max(12, (selected.displayEnd - selected.displayStart) * zoom),
                  borderColor: peer.color, backgroundColor: `${peer.color}25`,
                }}
                title={`${peer.name} 正在編輯此片段`}
              />
            )}
          </div>
        );
      })}
    </>
  );
}

export function CollaborationAvatarStrip({ peers }: Pick<CollaborationPresenceOverlayProps, "peers">) {
  if (!peers.length) return null;
  return <div className="pointer-events-none absolute right-2 top-2 z-[61] flex -space-x-2">{peers.slice(0, 5).map((peer) => <div key={peer.id} title={`${peer.name} 正在協作`} className="grid h-7 w-7 place-items-center overflow-hidden rounded-full border-2 border-zinc-950 text-[10px] font-bold text-white shadow" style={{ backgroundColor: peer.color }}>{peer.avatarUrl ? <img src={peer.avatarUrl} alt="" className="h-full w-full object-cover" /> : peer.name.slice(0, 1).toUpperCase()}</div>)}</div>;
}
