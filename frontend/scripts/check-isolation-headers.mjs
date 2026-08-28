/**
 * Verify that the *production build output* still declares the cross-origin
 * isolation headers, on every path the deployed frontend serves.
 *
 * `frontend/next.config.mjs` declares three headers for `/:path*`. Two of them
 * are load-bearing for the product's own client-side render path, not merely
 * hardening: `lib/client-render/ffmpeg-client-renderer.ts` and
 * `lib/client-render/ffmpeg-render.worker.ts` both branch on
 * `crossOriginIsolated`, which a document only gets from
 * `Cross-Origin-Opener-Policy: same-origin` *and*
 * `Cross-Origin-Embedder-Policy: require-corp` on its own response. Lose either
 * and `SharedArrayBuffer` disappears, the multithreaded FFmpeg path silently
 * stops being taken, and every browser render falls back to the slow one --
 * with no error, no failing build and no failing test.
 *
 * This reads `.next/routes-manifest.json` rather than `next.config.mjs`, because
 * that manifest -- not the config -- is what the running server answers from:
 * `next start` builds its header table from `routesManifest.headers`
 * (next/dist/server/lib/router-utils/filesystem.js). Asserting the config source
 * would still pass for a config whose `headers()` was never applied to the
 * build; asserting the manifest cannot.
 *
 * It also checks the compiled `regex` against concrete paths instead of
 * comparing `source` strings, so narrowing the rule to a subset of routes fails
 * here even though all three header names are still present.
 *
 *     node scripts/check-isolation-headers.mjs   # after `npm run build`
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const ROUTES_MANIFEST = fileURLToPath(new URL("../.next/routes-manifest.json", import.meta.url));

/** What makes a document cross-origin isolated. Values are compared exactly. */
export const ISOLATION_HEADERS = {
  "cross-origin-opener-policy": "same-origin",
  "cross-origin-embedder-policy": "require-corp",
};

/** Declared for every response; under `require-corp` it is what keeps subresources loadable. */
export const RESOURCE_POLICY_HEADERS = {
  "cross-origin-resource-policy": "same-origin",
};

/**
 * HTML responses. `crossOriginIsolated` is decided per document, so each of
 * these has to carry the isolation pair itself -- inheriting it from the
 * landing page is not a thing. The last entry is the 404 document: it is served
 * through the same header pipeline and is a real navigation target.
 */
export const DOCUMENT_PATHS = [
  "/",
  "/studio",
  "/mobile-preview",
  "/wireless-camera",
  "/a-path-that-does-not-exist",
];

/**
 * Subresources the isolated documents pull in. A route rule narrowed to the
 * document paths above would leave these uncovered, so they are checked
 * separately: the hashed client chunk that constructs the render Worker, the
 * service worker script, and the two WASM payloads under `public/`.
 */
export const SUBRESOURCE_PATHS = [
  "/_next/static/chunks/main-app-0123456789abcdef.js",
  "/media-range-cache-sw.js",
  "/ffmpeg-core/ffmpeg-core.wasm",
  "/ort/ort-wasm-simd-threaded.jsep.wasm",
];

/** A path no route can be enumerated for, so a rule that still covers it covers future routes too. */
export const ARBITRARY_NESTED_PATH = "/deeply/nested/future/route";

/**
 * Headers the manifest applies to `path`, lowercased. Entries carrying `has` or
 * `missing` only apply when the request matches those conditions, so they
 * cannot be counted as an unconditional guarantee.
 */
function headersFor(manifest, path) {
  const applied = new Map();
  for (const entry of manifest?.headers ?? []) {
    if (entry.has || entry.missing) continue;
    if (typeof entry.regex !== "string" || !new RegExp(entry.regex).test(path)) continue;
    for (const header of entry.headers ?? []) {
      applied.set(String(header.key).toLowerCase(), String(header.value).trim().toLowerCase());
    }
  }
  return applied;
}

function missingFrom(manifest, path, required) {
  const applied = headersFor(manifest, path);
  return Object.entries(required)
    .filter(([key, value]) => applied.get(key) !== value)
    .map(([key, value]) => `${path} is missing "${key}: ${value}" (got ${applied.get(key) ?? "no such header"})`);
}

/** @returns {string[]} one message per uncovered path/header pair; empty means the contract holds. */
export function checkIsolationHeaders(manifest) {
  const isolationAndResource = { ...ISOLATION_HEADERS, ...RESOURCE_POLICY_HEADERS };
  return [
    ...DOCUMENT_PATHS.flatMap((path) => missingFrom(manifest, path, isolationAndResource)),
    ...SUBRESOURCE_PATHS.flatMap((path) => missingFrom(manifest, path, RESOURCE_POLICY_HEADERS)),
    ...missingFrom(manifest, ARBITRARY_NESTED_PATH, isolationAndResource),
  ];
}

function main() {
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(ROUTES_MANIFEST, "utf8"));
  } catch (error) {
    console.error(`Could not read ${ROUTES_MANIFEST}: ${error.message}`);
    console.error("Run `npm run build` first; this checks the build output, not next.config.mjs.");
    return 1;
  }

  const problems = checkIsolationHeaders(manifest);
  if (problems.length > 0) {
    console.error("The production build does not declare the cross-origin isolation contract:");
    for (const problem of problems) console.error(`  - ${problem}`);
    console.error(
      "\nnext.config.mjs must keep declaring Cross-Origin-Opener-Policy: same-origin and\n" +
        "Cross-Origin-Embedder-Policy: require-corp for every path. Without them the deployed\n" +
        "frontend is not cross-origin isolated, SharedArrayBuffer is undefined, and the\n" +
        "multithreaded browser render in lib/client-render/ silently stops being used.",
    );
    return 1;
  }

  console.log("Cross-origin isolation headers are declared for every checked path.");
  return 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exit(main());
