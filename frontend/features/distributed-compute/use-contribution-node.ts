"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createComputeSessionKeyPair, deriveComputeSessionKey, encryptChunkFragments, exportComputePublicKey, sendWithBackpressure } from "@/lib/distributed-compute-protocol";

type ContributionState = "idle" | "joining" | "available" | "paused" | "error";
type Assignment = { assignment_id: string; ticket: string; manifest: Record<string, unknown>; result_upload_url: string };

type Options = {
  apiBaseUrl: string;
  nodeId?: string;
  /** The app must show an explicit opt-in control before invoking this hook's join method. */
  runChunk: (assignment: Assignment, encryptedInput: Uint8Array) => Promise<{ output: Blob; sha256: string; decodedFingerprint: string; rendererDigest: string; signature: string }>;
};

export function useContributionNode({ apiBaseUrl, nodeId, runChunk }: Options) {
  const [state, setState] = useState<ContributionState>("idle");
  const [error, setError] = useState<string | undefined>(undefined);
  const socketRef = useRef<WebSocket | null>(null);
  const peerRef = useRef<RTCPeerConnection | null>(null);

  const pause = useCallback(() => {
    socketRef.current?.close(); peerRef.current?.close();
    socketRef.current = null; peerRef.current = null; setState("paused");
  }, []);

  const acceptPeerInput = useCallback(async (assignment: Assignment, remotePublicKey: JsonWebKey, encryptedSource: Uint8Array) => {
    const pair = await createComputeSessionKeyPair();
    const sessionKey = await deriveComputeSessionKey(pair.privateKey, remotePublicKey);
    const output = await runChunk(assignment, encryptedSource);
    await fetch(assignment.result_upload_url, { method: "PUT", body: output.output, headers: { "content-type": "video/mp4" } });
    return { publicKey: await exportComputePublicKey(pair.publicKey), result: output };
  }, [runChunk]);

  const connectSignaling = useCallback((id: string) => {
    const wsUrl = apiBaseUrl.replace(/^http/, "ws") + `/api/v1/compute/nodes/${id}/signal`;
    const socket = new WebSocket(wsUrl); socketRef.current = socket;
    socket.onclose = () => setState((current) => current === "paused" ? current : "idle");
    socket.onerror = () => { setError("WebRTC signaling connection failed"); setState("error"); };
    return socket;
  }, [apiBaseUrl]);

  /** Called only by a visible user action; this hook never contributes in the background by default. */
  const start = useCallback(async () => {
    if (!nodeId) { setError("Register a compute node before contributing"); setState("error"); return; }
    setState("joining");
    try {
      connectSignaling(nodeId);
      setState("available");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to join compute pool"); setState("error");
    }
  }, [connectSignaling, nodeId]);

  /** Source peers call this after WebRTC negotiation; transport payload is AES-GCM fragmented. */
  const sendSourceToPeer = useCallback(async (channel: RTCDataChannel, assignmentId: string, peerPublicKey: JsonWebKey, source: Uint8Array) => {
    const pair = await createComputeSessionKeyPair();
    const sessionKey = await deriveComputeSessionKey(pair.privateKey, peerPublicKey);
    for (const fragment of await encryptChunkFragments(sessionKey, assignmentId, source)) await sendWithBackpressure(channel, fragment);
    return exportComputePublicKey(pair.publicKey);
  }, []);

  useEffect(() => () => pause(), [pause]);
  return { state, error, start, pause, acceptPeerInput, sendSourceToPeer };
}
