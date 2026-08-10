/**
 * Application-layer encryption for volunteer compute chunks.
 * RTCDataChannel already uses DTLS, but this protects payloads from a relay/TURN
 * operator and binds every encrypted fragment to one signed assignment ticket.
 */

const MAX_FRAGMENT_BYTES = 12 * 1024;
const encoder = new TextEncoder();

export type EncryptedFragment = {
  assignmentId: string;
  index: number;
  total: number;
  iv: string;
  payload: string;
};

const toBase64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes));
const fromBase64 = (value: string) => Uint8Array.from(atob(value), (item) => item.charCodeAt(0));

export async function createComputeSessionKeyPair(): Promise<CryptoKeyPair> {
  return crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveKey"]);
}

export async function exportComputePublicKey(key: CryptoKey): Promise<JsonWebKey> {
  return crypto.subtle.exportKey("jwk", key);
}

export async function deriveComputeSessionKey(privateKey: CryptoKey, peerPublicJwk: JsonWebKey): Promise<CryptoKey> {
  const peer = await crypto.subtle.importKey("jwk", peerPublicJwk, { name: "ECDH", namedCurve: "P-256" }, false, []);
  return crypto.subtle.deriveKey({ name: "ECDH", public: peer }, privateKey, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

export async function encryptChunkFragments(key: CryptoKey, assignmentId: string, bytes: Uint8Array): Promise<EncryptedFragment[]> {
  const total = Math.ceil(bytes.byteLength / MAX_FRAGMENT_BYTES);
  return Promise.all(Array.from({ length: total }, async (_, index) => {
    const part = bytes.slice(index * MAX_FRAGMENT_BYTES, Math.min(bytes.byteLength, (index + 1) * MAX_FRAGMENT_BYTES));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const additionalData = encoder.encode(`${assignmentId}:${index}:${total}`);
    const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData }, key, part);
    return { assignmentId, index, total, iv: toBase64(iv), payload: toBase64(new Uint8Array(cipher)) };
  }));
}

export async function decryptChunkFragment(key: CryptoKey, fragment: EncryptedFragment): Promise<Uint8Array> {
  const additionalData = encoder.encode(`${fragment.assignmentId}:${fragment.index}:${fragment.total}`);
  const clear = await crypto.subtle.decrypt({ name: "AES-GCM", iv: fromBase64(fragment.iv), additionalData }, key, fromBase64(fragment.payload));
  return new Uint8Array(clear);
}

export async function sendWithBackpressure(channel: RTCDataChannel, fragment: EncryptedFragment): Promise<void> {
  while (channel.bufferedAmount > 512 * 1024) {
    await new Promise<void>((resolve) => {
      const onLow = () => { channel.removeEventListener("bufferedamountlow", onLow); resolve(); };
      channel.bufferedAmountLowThreshold = 128 * 1024;
      channel.addEventListener("bufferedamountlow", onLow, { once: true });
    });
  }
  channel.send(JSON.stringify({ type: "encrypted_chunk_fragment", fragment }));
}
