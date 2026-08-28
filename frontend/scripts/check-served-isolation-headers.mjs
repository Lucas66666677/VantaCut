/**
 * Verify that the *public host* actually serves the cross-origin isolation
 * headers -- not that the build declared them.
 *
 * `check-isolation-headers.mjs` reads `.next/routes-manifest.json`, which is
 * what `next start` answers from. That is a real link in the chain, but it is
 * not the last one: the response a browser receives is produced by the origin
 * *and* everything in front of it. Render publishes every service through
 * Cloudflare, and a request that never reaches the Next.js process at all -- a
 * free-plan instance still spinning up, a failed deploy, an origin the edge
 * cannot reach -- is answered in front of it, with none of these headers on it.
 * A build-output assertion passes in exactly that state, because the build is
 * fine; what is broken is downstream of it.
 *
 * So this script asserts nothing about this repository. It makes real requests
 * to a real origin and reads the real response headers. It is the only check
 * here that can tell "the build declares the contract" from "visitors receive
 * it".
 *
 *     node scripts/check-served-isolation-headers.mjs
 *     node scripts/check-served-isolation-headers.mjs https://staging.example.com
 *     FRONTEND_ORIGIN=https://staging.example.com node scripts/check-served-isolation-headers.mjs
 *
 * It fails closed. An origin that does not answer, answers 5xx, or answers
 * without the headers is a failure -- never a skip. Reporting "could not
 * verify" as success would reintroduce the exact hole this exists to close:
 * passing while the served response lacks the headers.
 *
 * Deliberately NOT wired into pull-request CI. At PR time the deployed origin
 * still serves `main`, so a PR job probing it would report on code the PR does
 * not contain. This belongs after a deploy -- see the manually dispatched
 * `.github/workflows/frontend-served-isolation-headers.yml`.
 */

import { pathToFileURL } from "node:url";

/**
 * The frontend's public origin, from `render.yaml`'s `vantacut-frontend` web
 * service: Render publishes a web service at https://<name>.onrender.com, and
 * `scripts/release_preflight.py`'s `check_render_public_origins` already holds
 * the blueprint to that. The sibling test fails if the service is renamed.
 */
export const DEFAULT_ORIGIN = "https://vantacut-frontend.onrender.com";

/** What makes a document cross-origin isolated, plus the policy that keeps its subresources loadable. */
export const ISOLATION_AND_RESOURCE = {
  "cross-origin-opener-policy": "same-origin",
  "cross-origin-embedder-policy": "require-corp",
  "cross-origin-resource-policy": "same-origin",
};

/** A subresource is not a document; under `require-corp` it only has to opt in to being loaded. */
export const RESOURCE_POLICY = {
  "cross-origin-resource-policy": "same-origin",
};

/**
 * `status: "ok"` demands 200 -- an enumerated asset that 404s makes the header
 * on it meaningless, because the payload the isolated document needs is not
 * there. `status: "served"` demands only that the application answered at all,
 * so the deliberate 404 document still has to carry the isolation pair.
 *
 * Documents are fetched with GET, the method a browser navigation uses and the
 * one whose response decides `crossOriginIsolated`. Subresources are fetched
 * with HEAD: the two WASM payloads are ~26MB and ~31MB, and this needs their
 * headers, not their bytes.
 */
export const PROBES = [
  { path: "/", method: "GET", status: "ok", requires: ISOLATION_AND_RESOURCE },
  { path: "/studio", method: "GET", status: "ok", requires: ISOLATION_AND_RESOURCE },
  { path: "/mobile-preview", method: "GET", status: "ok", requires: ISOLATION_AND_RESOURCE },
  { path: "/wireless-camera", method: "GET", status: "ok", requires: ISOLATION_AND_RESOURCE },
  // Not an oversight: the 404 document is a real navigation target served
  // through the same header pipeline, and it is the one path no route rule can
  // be enumerated for -- so a rule narrowed to the known routes fails here.
  { path: "/a-path-that-does-not-exist", method: "GET", status: "served", requires: ISOLATION_AND_RESOURCE },
  { path: "/media-range-cache-sw.js", method: "HEAD", status: "ok", requires: RESOURCE_POLICY },
  { path: "/audio-meter.worklet.js", method: "HEAD", status: "ok", requires: RESOURCE_POLICY },
  { path: "/ffmpeg-core/ffmpeg-core.wasm", method: "HEAD", status: "ok", requires: RESOURCE_POLICY },
  { path: "/ort/ort-wasm-simd-threaded.jsep.wasm", method: "HEAD", status: "ok", requires: RESOURCE_POLICY },
];

/** Header names that identify a response as having come from the application rather than from in front of it. */
const APPLICATION_MARKERS = ["x-powered-by", "x-nextjs-cache", "x-render-origin-server", "rndr-id"];

/** Lowercased name -> lowercased, trimmed value. Accepts a `Headers`, a `Map`, or a plain object. */
export function normalizeHeaders(headers) {
  const normalized = new Map();
  if (!headers) return normalized;
  const pairs = [];
  if (typeof headers.forEach === "function" && typeof headers.get === "function") {
    headers.forEach((value, key) => pairs.push([key, value]));
  } else {
    pairs.push(...Object.entries(headers));
  }
  for (const [key, value] of pairs) {
    normalized.set(String(key).toLowerCase(), String(value).trim().toLowerCase());
  }
  return normalized;
}

/**
 * Whether this response was produced by the application. Used only to explain a
 * failure, never to excuse one: a response missing the headers is a failure
 * either way, and this only says which of the two problems it is.
 */
export function isApplicationResponse(headers) {
  const normalized = normalizeHeaders(headers);
  return APPLICATION_MARKERS.some((marker) => normalized.has(marker));
}

/** @returns {string[]} one message per violation; empty means this probe's contract holds. */
export function evaluateProbe(probe, result) {
  const label = `${probe.method} ${probe.path}`;
  if (!result || result.error) {
    return [`${label}: no response from the origin (${result?.error ?? "not attempted"})`];
  }

  const problems = [];
  if (probe.status === "ok" ? result.status !== 200 : result.status >= 500) {
    const expected = probe.status === "ok" ? "200" : "a response from the application";
    problems.push(`${label}: expected ${expected}, got ${result.status}`);
  }

  const headers = normalizeHeaders(result.headers);
  for (const [key, value] of Object.entries(probe.requires)) {
    const observed = headers.get(key);
    if (observed !== value) {
      problems.push(`${label}: missing "${key}: ${value}" (got ${observed ?? "no such header"})`);
    }
  }
  return problems;
}

/** @returns {string[]} every violation across every probe. */
export function evaluateProbes(results) {
  return results.flatMap(({ probe, result }) => evaluateProbe(probe, result));
}

/** Rejects anything that is not a bare https origin, so a path or a http:// target cannot be probed by mistake. */
export function requestUrl(origin, path) {
  const base = new URL(origin);
  if (base.protocol !== "https:") throw new Error(`origin must be https, got ${origin}`);
  if (base.pathname !== "/" || base.search || base.hash) {
    throw new Error(`origin must be a bare origin with no path, got ${origin}`);
  }
  return new URL(path, base).toString();
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Fetch one probe, retrying only while the origin is not answering as the
 * application. A free-plan Render service that has scaled to zero takes tens of
 * seconds to answer its first request, and treating that as a verdict would
 * make this report a header regression that is not there. Retries are bounded,
 * and running out of them is a failure, not a pass.
 */
async function fetchProbe(origin, probe, { fetchImpl, attempts, timeoutMs, sleep }) {
  let last = { error: "not attempted" };
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetchImpl(requestUrl(origin, probe.path), {
        method: probe.method,
        redirect: "manual",
        signal: AbortSignal.timeout(timeoutMs),
      });
      // Headers are all this needs; do not pull a 31MB body through the socket.
      if (response.body) await response.body.cancel().catch(() => {});
      last = { status: response.status, headers: response.headers };
      if (response.status < 500) return last;
    } catch (error) {
      last = { error: error?.message ?? String(error) };
    }
    if (attempt < attempts) await sleep(2000 * attempt);
  }
  return last;
}

export async function probeOrigin(origin, options = {}) {
  const settings = {
    fetchImpl: options.fetchImpl ?? globalThis.fetch,
    attempts: options.attempts ?? 3,
    timeoutMs: options.timeoutMs ?? 90_000,
    sleep: options.sleep ?? wait,
  };
  const results = [];
  for (const probe of PROBES) {
    results.push({ probe, result: await fetchProbe(origin, probe, settings) });
  }
  return results;
}

async function main(argv = process.argv, env = process.env) {
  const origin = (argv[2] ?? env.FRONTEND_ORIGIN ?? DEFAULT_ORIGIN).replace(/\/+$/, "");
  console.log(`Checking the cross-origin isolation headers served by ${origin}`);

  let results;
  try {
    results = await probeOrigin(origin);
  } catch (error) {
    console.error(`Cannot probe ${origin}: ${error.message}`);
    return 1;
  }

  const problems = evaluateProbes(results);
  if (problems.length === 0) {
    for (const { probe, result } of results) {
      console.log(`  ok  ${probe.method} ${probe.path} -> ${result.status}`);
    }
    console.log(`${origin} serves the cross-origin isolation contract on every checked path.`);
    return 0;
  }

  console.error(`${origin} does not serve the cross-origin isolation contract:`);
  for (const problem of problems) console.error(`  - ${problem}`);

  const answeredAsTheApp = results.some(
    ({ result }) => result && !result.error && isApplicationResponse(result.headers),
  );
  console.error(
    answeredAsTheApp
      ? "\nThe application answered, but without what a document needs to be cross-origin\n" +
          "isolated. SharedArrayBuffer is undefined for those visitors and the multithreaded\n" +
          "browser render in lib/client-render/ silently falls back to the single-threaded\n" +
          "path. Check next.config.mjs, then that the running image was built from it."
      : "\nNone of these responses carry a marker of the application itself, so they were most\n" +
          "likely answered in front of it -- a free-plan instance still starting up, a failed\n" +
          "deploy, or an origin the edge cannot reach. The build output can be entirely correct\n" +
          "in that state, and no amount of rebuilding fixes it; get the service answering first.",
  );
  return 1;
}

// Only run when invoked directly, so the tests can import the pure parts.
// `pathToFileURL` rather than string-building a file: URL, so this also holds on
// Windows, where argv[1] is a drive-letter path.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(await main());
}
