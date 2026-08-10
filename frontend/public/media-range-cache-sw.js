/* Persistent byte-range cache for editor proxies. This file is intentionally dependency-free. */
const CACHE_NAME = "ai-video-media-ranges-v1";
const MEDIA_PATTERN = /\.(mp4|mov|m4v|webm|m4s|mp3|m4a|aac|wav|ogg)(?:$|[?#])/i;

function isMediaRequest(request) { return MEDIA_PATTERN.test(new URL(request.url).pathname); }
function cacheKey(url, range) {
  const key = new URL("/__media-range-cache__", self.location.origin);
  key.searchParams.set("url", url); key.searchParams.set("range", range);
  return new Request(key.toString());
}
async function cacheRange(url, range) {
  const cache = await caches.open(CACHE_NAME); const key = cacheKey(url, range); const hit = await cache.match(key);
  if (hit) return hit;
  const response = await fetch(new Request(url, { headers: { Range: range }, mode: "cors", credentials: "omit" }));
  // Never cache a server's accidental full-asset 200 response.
  if (response.status === 206) { try { await cache.put(key, response.clone()); } catch { /* quota/CORS: use network response */ } }
  return response;
}

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  const range = event.request.headers.get("range");
  if (event.request.method !== "GET" || !range || !isMediaRequest(event.request)) return;
  event.respondWith(cacheRange(event.request.url, range));
});
self.addEventListener("message", (event) => {
  const data = event.data;
  if (data?.type === "precache-media-range" && typeof data.url === "string" && typeof data.range === "string") event.waitUntil(cacheRange(data.url, data.range).catch(() => undefined));
});
