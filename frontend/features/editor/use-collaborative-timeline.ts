"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as Y from "yjs";

import { useTimelineStore } from "@/features/editor/timeline-store";
import { useAuthStore } from "@/lib/auth/auth-store";
import type { TimelineClipInput } from "@/types/timeline";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const REMOTE_UPDATE = Symbol("remote-yjs-update");
const LOCK_TTL_MS = 8_000;

export interface CollaborationUser {
  id: string;
  name: string;
  color: string;
}

export interface CollaboratorPresence extends CollaborationUser {
  playheadTime: number;
  selectedClipId?: string;
  lockedClipId?: string;
  cursor?: { x: number; y: number; surface: "timeline" | "preview" };
  avatarUrl?: string;
  lastSeenAt: number;
}

interface UseCollaborativeTimelineOptions {
  timelineId?: string;
  currentUser: CollaborationUser;
  initialTimeline: TimelineClipInput[];
}

type ClipMap = Y.Map<unknown>;

function websocketUrl(timelineId: string): string {
  const base = new URL(API_BASE_URL);
  base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  base.pathname = `/api/v1/timelines/${timelineId}/collaboration`;
  return base.toString();
}

function toClipInput(id: string, clip: ClipMap): TimelineClipInput {
  return { ...(clip.toJSON() as Omit<TimelineClipInput, "id">), id };
}

export function useCollaborativeTimeline({ timelineId, currentUser, initialTimeline }: UseCollaborativeTimelineOptions) {
  const docRef = useRef<Y.Doc | undefined>(undefined);
  if (!docRef.current) docRef.current = new Y.Doc();
  const doc = docRef.current;
  const clips = useMemo(() => doc.getMap<ClipMap>("clips"), [doc]);
  const locks = useMemo(() => doc.getMap<{ ownerId: string; expiresAt: number }>("clipLocks"), [doc]);
  const websocketRef = useRef<WebSocket | undefined>(undefined);
  const presenceRef = useRef<Partial<CollaboratorPresence>>({ playheadTime: 0 });
  const lastPresenceSentAtRef = useRef(0);
  const [peers, setPeers] = useState<Record<string, CollaboratorPresence>>({});
  const [lockState, setLockState] = useState<Record<string, { ownerId: string; expiresAt: number }>>({});
  const applyCollaborationTimeline = useTimelineStore((state) => state.applyCollaborationTimeline);
  // Selecting `token` (rather than reading useAuthStore.getState() once) is
  // deliberate, matching features/project-status/use-project-status.ts: it
  // makes the connection effect below re-run whenever the session changes
  // (e.g. after logout/login), so a token obtained after this component
  // first mounted is still picked up, and the socket is torn down on logout
  // instead of being left open with a now-invalid credential.
  const token = useAuthStore((state) => state.token);

  const sendPresence = useCallback((patch: Partial<Omit<CollaboratorPresence, keyof CollaborationUser | "lastSeenAt">>, force = false) => {
    const websocket = websocketRef.current;
    if (websocket?.readyState !== WebSocket.OPEN) return;
    const now = Date.now();
    if (!force && now - lastPresenceSentAtRef.current < 33) return;
    presenceRef.current = { ...presenceRef.current, ...patch, playheadTime: patch.playheadTime ?? presenceRef.current.playheadTime ?? useTimelineStore.getState().playheadTime };
    lastPresenceSentAtRef.current = now;
    websocket.send(JSON.stringify({ kind: "presence", user: currentUser, ...presenceRef.current, lastSeenAt: now }));
  }, [currentUser]);

  useEffect(() => {
    if (clips.size === 0 && timelineId) {
      doc.transact(() => {
        for (const clip of initialTimeline) {
          const id = clip.id ?? crypto.randomUUID();
          const sharedClip = new Y.Map<unknown>();
          const { id: _ignoredId, ...clipFields } = clip;
          Object.entries(clipFields).forEach(([key, value]) => sharedClip.set(key, value));
          clips.set(id, sharedClip);
        }
      });
    }
    const syncZustand = () => {
      const next = Array.from(clips.entries())
        .map(([id, clip]) => toClipInput(id, clip))
        .sort((left, right) => (left.timeline_start ?? left.source_start) - (right.timeline_start ?? right.source_start));
      applyCollaborationTimeline(next);
    };
    syncZustand();
    clips.observeDeep(syncZustand);
    return () => clips.unobserveDeep(syncZustand);
  }, [applyCollaborationTimeline, clips, doc, initialTimeline, timelineId]);

  useEffect(() => {
    const syncLocks = () => setLockState(Object.fromEntries(locks.entries()));
    syncLocks();
    locks.observe(syncLocks);
    return () => locks.unobserve(syncLocks);
  }, [locks]);

  useEffect(() => {
    if (!timelineId || !token) return;
    // Backend convention (Sec-WebSocket-Protocol — see
    // backend/app/api/v1/collaboration.py, which reuses
    // project_status.py's _authenticate_websocket): browsers cannot set a
    // custom Authorization header on the WebSocket constructor, so the
    // access token travels as the second offered subprotocol instead.
    // Never as a `?token=` query string, and never logged.
    const websocket = new WebSocket(websocketUrl(timelineId), ["bearer", token]);
    websocket.binaryType = "arraybuffer";
    websocketRef.current = websocket;
    const onUpdate = (update: Uint8Array, origin: unknown) => {
      if (origin !== REMOTE_UPDATE && websocket.readyState === WebSocket.OPEN) websocket.send(update);
    };
    doc.on("update", onUpdate);
    websocket.onopen = () => sendPresence({ playheadTime: useTimelineStore.getState().playheadTime }, true);
    websocket.onmessage = (event) => {
      if (typeof event.data === "string") {
        const message = JSON.parse(event.data) as { kind?: string; user?: CollaborationUser; playheadTime?: number; selectedClipId?: string; lockedClipId?: string; cursor?: CollaboratorPresence["cursor"]; avatarUrl?: string; lastSeenAt?: number };
        if (message.kind !== "presence" || !message.user || message.user.id === currentUser.id) return;
        setPeers((previous) => ({
          ...previous,
          [message.user!.id]: {
            ...message.user!, playheadTime: message.playheadTime ?? 0,
            selectedClipId: message.selectedClipId,
            lockedClipId: message.lockedClipId,
            cursor: message.cursor,
            avatarUrl: message.avatarUrl,
            lastSeenAt: message.lastSeenAt ?? Date.now(),
          },
        }));
        return;
      }
      Y.applyUpdate(doc, new Uint8Array(event.data as ArrayBuffer), REMOTE_UPDATE);
    };
    return () => {
      doc.off("update", onUpdate);
      websocket.close();
      websocketRef.current = undefined;
    };
  }, [currentUser, doc, sendPresence, timelineId, token]);

  useEffect(() => useTimelineStore.subscribe((state, previous) => {
    if (state.playheadTime !== previous.playheadTime || state.selectedClipId !== previous.selectedClipId) {
      sendPresence({ playheadTime: state.playheadTime, selectedClipId: state.selectedClipId ?? undefined });
    }
  }), [sendPresence]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      const cutoff = Date.now() - LOCK_TTL_MS;
      setPeers((previous) => Object.fromEntries(Object.entries(previous).filter(([, peer]) => peer.lastSeenAt >= cutoff)));
    }, 2_000);
    return () => window.clearInterval(interval);
  }, []);

  const beginClipIntent = useCallback((clipId: string) => {
    const now = Date.now();
    const existing = locks.get(clipId);
    // A competing intent remains editable. The lock is only visual coordination;
    // Yjs still converges field updates instead of rejecting somebody's work.
    if (!existing || existing.ownerId === currentUser.id || existing.expiresAt <= now) {
      doc.transact(() => locks.set(clipId, { ownerId: currentUser.id, expiresAt: now + LOCK_TTL_MS }));
    }
    const accepted = locks.get(clipId)?.ownerId === currentUser.id;
    sendPresence({ playheadTime: useTimelineStore.getState().playheadTime, selectedClipId: clipId, lockedClipId: clipId }, true);
    return accepted;
  }, [currentUser.id, doc, locks, sendPresence]);

  const endClipIntent = useCallback((clipId: string) => {
    if (locks.get(clipId)?.ownerId === currentUser.id) doc.transact(() => locks.delete(clipId));
    sendPresence({ playheadTime: useTimelineStore.getState().playheadTime, selectedClipId: undefined, lockedClipId: undefined });
  }, [currentUser.id, doc, locks, sendPresence]);

  const updateClip = useCallback((clipId: string, patch: Partial<TimelineClipInput>) => {
    beginClipIntent(clipId);
    const clip = clips.get(clipId);
    if (!clip) return false;
    doc.transact(() => Object.entries(patch).forEach(([key, value]) => clip.set(key, value)));
    return true;
  }, [beginClipIntent, clips, doc]);

  const activePeers = Object.values(peers).filter((peer) => peer.lastSeenAt >= Date.now() - LOCK_TTL_MS);
  const isLockedByOther = useCallback((clipId: string) => {
    const lock = lockState[clipId];
    return Boolean(lock && lock.ownerId !== currentUser.id && lock.expiresAt > Date.now());
  }, [currentUser.id, lockState]);

  return { doc, peers: activePeers, updateClip, beginClipIntent, endClipIntent, isLockedByOther, sendPresence };
}
