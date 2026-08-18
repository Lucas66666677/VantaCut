"use client";

import { useEffect } from "react";

import {
  type ProjectStatusEvent,
  useProjectStatusStore,
} from "@/features/project-status/project-status-store";
import { useAuthStore } from "@/lib/auth/auth-store";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const INITIAL_RETRY_DELAY_MS = 1_000;
const MAX_RETRY_DELAY_MS = 15_000;
type StatusTransport = "sse" | "websocket";

/**
 * Parses the `text/event-stream` body produced by
 * backend/app/api/v1/project_status.py's `project_status_events` generator
 * (`event: status\ndata: {...}\n\n`, plus `: keepalive\n\n` comment lines)
 * out of a raw fetch() streaming reader. This is intentionally a tiny,
 * purpose-built parser for this one event shape, not a generic SSE client —
 * native EventSource already handles the generic case; the only reason this
 * exists at all is that EventSource cannot attach an Authorization header,
 * which the backend now requires (Batch 1, PR #2).
 */
async function consumeEventStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: (eventName: string, data: string) => void,
): Promise<void> {
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex !== -1) {
      const rawEvent = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      let eventName = "message";
      let data: string | undefined;
      for (const line of rawEvent.split("\n")) {
        if (line.startsWith(":")) continue; // SSE comment / keepalive, no payload.
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) data = line.slice(5).trim();
      }
      if (data !== undefined) onEvent(eventName, data);
      separatorIndex = buffer.indexOf("\n\n");
    }
  }
}

export function useProjectStatus(projectId: string | null | undefined, transport: StatusTransport = "sse"): ProjectStatusEvent | undefined {
  const status = useProjectStatusStore((state) => (projectId ? state.projects[projectId] : undefined));
  const setProjectStatus = useProjectStatusStore((state) => state.setProjectStatus);
  const setConnectionState = useProjectStatusStore((state) => state.setConnectionState);

  // Selecting `token` (rather than reading useAuthStore.getState() once) is
  // deliberate: it makes this effect re-run — tearing down the old
  // connection and opening a new one — whenever the session changes, e.g.
  // after logout/login. Without this, a token obtained after this component
  // first mounted would never be picked up.
  const token = useAuthStore((state) => state.token);

  useEffect(() => {
    if (!projectId || !token) return;
    // Rebind to a definitely-non-null const so the closures below
    // (connectWebsocket/connectSse, defined further down) have an
    // unambiguous `string` type for the credential, independent of how far
    // TypeScript's control-flow narrowing follows `token` into them.
    const authToken = token;

    let websocket: WebSocket | undefined;
    let abortController: AbortController | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let retryDelay = INITIAL_RETRY_DELAY_MS;

    const connect = () => {
      if (disposed) return;
      if (transport === "websocket") {
        connectWebsocket();
      } else {
        connectSse();
      }
    };

    const scheduleReconnect = () => {
      if (disposed) return;
      reconnectTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(MAX_RETRY_DELAY_MS, retryDelay * 2);
    };

    const connectWebsocket = () => {
      const url = new URL(API_URL);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      url.pathname = `/api/v1/projects/${projectId}/status/ws`;
      // Backend convention (Sec-WebSocket-Protocol, reviewed and kept
      // unchanged by this PR — see project_status.py's
      // _authenticate_websocket): browsers cannot set a custom
      // Authorization header on the WebSocket constructor, so the access
      // token travels as the second offered subprotocol instead. Never as
      // a `?token=` query string (query strings land in history/Referer/
      // access logs) and never logged.
      const socket = new WebSocket(url.toString(), ["bearer", authToken]);
      websocket = socket;

      socket.onopen = () => {
        retryDelay = INITIAL_RETRY_DELAY_MS;
        setConnectionState(projectId, true);
      };

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(String(event.data)) as ProjectStatusEvent & { kind?: string };
          if (payload.kind !== "keepalive") setProjectStatus({ ...payload, connected: true });
        } catch {
          // Ignore malformed transient messages and continue listening.
        }
      };

      socket.onclose = (event) => {
        setConnectionState(projectId, false);
        if (disposed) return;
        if (event.code === 1008) {
          // WS_1008_POLICY_VIOLATION: the backend uses this single code for
          // both "no/invalid credential" and "authenticated but not this
          // project's owner" (deliberately, to avoid leaking which case
          // applies — see project_status.py). Only the first case actually
          // means the session itself is bad; the second just means this
          // project id isn't accessible to this user. Since the frontend
          // can't tell them apart from the close code alone, the safe
          // behavior is to stop retrying THIS connection without guessing —
          // and, importantly, without calling handleUnauthorized() here,
          // which would incorrectly log a validly-authenticated user out of
          // the whole app merely for viewing a project they don't own. If
          // the token itself really is invalid, any other authenticated
          // call (via lib/api/authenticated-fetch.ts) will independently
          // hit a 401 and trigger the real session-expired handling then.
          return;
        }
        scheduleReconnect();
      };
    };

    const connectSse = async () => {
      const controller = new AbortController();
      abortController = controller;
      try {
        const response = await fetch(`${API_URL}/api/v1/projects/${projectId}/status`, {
          headers: { Authorization: `Bearer ${authToken}` },
          signal: controller.signal,
        });

        if (response.status === 401 || response.status === 403) {
          // Missing/invalid/expired token, or a disabled account: the
          // session itself is no longer usable anywhere in the app, so this
          // is the one case where clearing it globally is correct.
          useAuthStore.getState().handleUnauthorized();
          setConnectionState(projectId, false);
          return; // terminal: retrying against a rejected token would just repeat.
        }
        if (response.status === 404) {
          // Valid session, but not this project's owner (or it doesn't
          // exist) — same non-enumerating response either way. Stop this
          // connection without touching the session; see the WS 1008
          // handling above for the identical reasoning.
          setConnectionState(projectId, false);
          return;
        }
        if (!response.ok || !response.body) {
          throw new Error(`status stream request failed (${response.status})`);
        }

        retryDelay = INITIAL_RETRY_DELAY_MS;
        setConnectionState(projectId, true);
        const reader = response.body.getReader();
        await consumeEventStream(reader, (eventName, data) => {
          if (eventName !== "status") return;
          try {
            const payload = JSON.parse(data) as ProjectStatusEvent;
            setProjectStatus({ ...payload, connected: true });
          } catch {
            // Ignore malformed transient messages and continue listening.
          }
        });
        // Stream ended (server closed it, e.g. request.is_disconnected());
        // fall through to the transient-error reconnect path below.
        setConnectionState(projectId, false);
      } catch {
        if (controller.signal.aborted) return; // unmount/cleanup, not a real error.
        setConnectionState(projectId, false);
      }
      scheduleReconnect();
    };

    connect();
    return () => {
      disposed = true;
      abortController?.abort();
      websocket?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      setConnectionState(projectId, false);
    };
  }, [projectId, setConnectionState, setProjectStatus, transport, token]);

  return status;
}
