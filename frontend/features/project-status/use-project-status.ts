"use client";

import { useEffect } from "react";

import {
  type ProjectStatusEvent,
  useProjectStatusStore,
} from "@/features/project-status/project-status-store";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const INITIAL_RETRY_DELAY_MS = 1_000;
const MAX_RETRY_DELAY_MS = 15_000;
type StatusTransport = "sse" | "websocket";

export function useProjectStatus(projectId: string | null | undefined, transport: StatusTransport = "sse"): ProjectStatusEvent | undefined {
  const status = useProjectStatusStore((state) => (projectId ? state.projects[projectId] : undefined));
  const setProjectStatus = useProjectStatusStore((state) => state.setProjectStatus);
  const setConnectionState = useProjectStatusStore((state) => state.setConnectionState);

  useEffect(() => {
    if (!projectId) return;

    let eventSource: EventSource | undefined;
    let websocket: WebSocket | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let retryDelay = INITIAL_RETRY_DELAY_MS;

    const connect = () => {
      if (disposed) return;
      if (transport === "websocket") {
        const url = new URL(API_URL);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        url.pathname = `/api/v1/projects/${projectId}/status/ws`;
        websocket = new WebSocket(url.toString());
        websocket.onopen = () => {
          retryDelay = INITIAL_RETRY_DELAY_MS;
          setConnectionState(projectId, true);
        };
        websocket.onmessage = (event) => {
          try {
            const payload = JSON.parse(String(event.data)) as ProjectStatusEvent & { kind?: string };
            if (payload.kind !== "keepalive") setProjectStatus({ ...payload, connected: true });
          } catch {
            // Ignore malformed transient messages and continue listening.
          }
        };
        websocket.onclose = () => {
          setConnectionState(projectId, false);
          if (disposed) return;
          reconnectTimer = setTimeout(connect, retryDelay);
          retryDelay = Math.min(MAX_RETRY_DELAY_MS, retryDelay * 2);
        };
        return;
      }
      eventSource = new EventSource(`${API_URL}/api/v1/projects/${projectId}/status`);

      eventSource.onopen = () => {
        retryDelay = INITIAL_RETRY_DELAY_MS;
        setConnectionState(projectId, true);
      };

      eventSource.addEventListener("status", (event: MessageEvent<string>) => {
        try {
          const payload = JSON.parse(event.data) as ProjectStatusEvent;
          setProjectStatus({ ...payload, connected: true });
        } catch {
          // Ignore malformed transient messages and continue listening.
        }
      });

      eventSource.onerror = () => {
        eventSource?.close();
        setConnectionState(projectId, false);
        if (disposed) return;
        reconnectTimer = setTimeout(connect, retryDelay);
        retryDelay = Math.min(MAX_RETRY_DELAY_MS, retryDelay * 2);
      };
    };

    connect();
    return () => {
      disposed = true;
      eventSource?.close();
      websocket?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      setConnectionState(projectId, false);
    };
  }, [projectId, setConnectionState, setProjectStatus, transport]);

  return status;
}
