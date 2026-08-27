/**
 * Build-time guard: refuse to produce a production frontend image whose backend
 * API origin is loopback, private, or otherwise unusable from a real browser.
 *
 * Next.js inlines every `NEXT_PUBLIC_*` value into the client bundle at BUILD
 * time, so a wrong or missing value cannot be corrected afterwards by editing
 * the service's environment -- it is baked into the JavaScript that ships. And
 * every caller in this app supplies its own fallback, e.g.
 *
 *     const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
 *
 * which means a build with the variable absent does not fail, or warn, or even
 * look different: it succeeds and quietly hard-codes the *visitor's own
 * machine* as the API. Every request then fails in the browser, far from the
 * build that caused it. `render.yaml` already documents this hazard in prose;
 * this script is the part that enforces it.
 *
 * Both variable names below are checked because the source reads BOTH, which is
 * how this gap was found rather than assumed:
 *
 *     $ grep -ro "process\.env\.NEXT_PUBLIC_API[A-Z_]*" frontend | sort | uniq -c
 *          52 process.env.NEXT_PUBLIC_API_URL
 *          13 process.env.NEXT_PUBLIC_API_BASE_URL
 *
 * `NEXT_PUBLIC_API_BASE_URL` was not set anywhere in the repository -- not in
 * `render.yaml`, `.env.production.example`, `docker-compose.production.yml`, nor
 * `Dockerfile.production` -- so the twelve modules that read only that name
 * (the thirteenth falls back to `NEXT_PUBLIC_API_URL`) were shipping
 * `http://localhost:8000` in the live production bundle. `Dockerfile.production`
 * now derives it from `NEXT_PUBLIC_API_URL` when it is not supplied explicitly,
 * which both fixes those callers and makes this check satisfiable without
 * adding a second value to any dashboard.
 *
 * Deliberately NOT wired into `npm run build`. The `frontend-build` CI job
 * builds with `NEXT_PUBLIC_API_URL: http://127.0.0.1:3000` on purpose -- it is
 * proving the app compiles and never contacts a backend -- and that is a
 * legitimate loopback build. Only an image built from `Dockerfile.production`
 * is unambiguously destined for production, so that is the single place this
 * runs.
 *
 * Holds no secret and touches no network: it inspects the shape of a URL only,
 * so it behaves identically in CI, in Docker, and on a laptop.
 *
 *     node scripts/check-public-api-origin.mjs
 */

import { pathToFileURL } from "node:url";

/** Variables the frontend source actually reads for the backend origin. */
export const REQUIRED_API_ORIGIN_VARS = [
  "NEXT_PUBLIC_API_URL",
  "NEXT_PUBLIC_API_BASE_URL",
];

const LOOPBACK_HOSTNAMES = new Set(["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "[::]"]);

/** IPv4 dotted quad; returns null for anything that is not exactly four octets. */
function parseIPv4(hostname) {
  const parts = hostname.split(".");
  if (parts.length !== 4) return null;
  const octets = [];
  for (const part of parts) {
    // Reject "01", "+1", "1e2", "" -- URL hosts are canonicalised, but this is
    // a guard, so it should not depend on that.
    if (!/^\d{1,3}$/.test(part)) return null;
    const value = Number(part);
    if (value > 255) return null;
    octets.push(value);
  }
  return octets;
}

/**
 * Classify a hostname as publicly routable or not.
 *
 * Returns a reason string when the host cannot serve a public production API,
 * or null when it is acceptable.
 */
function rejectHostname(hostname) {
  const host = hostname.toLowerCase();

  if (LOOPBACK_HOSTNAMES.has(host) || host === "localhost." || host.endsWith(".localhost")) {
    return "is a loopback address; the browser would call the visitor's own machine";
  }
  // mDNS and private-suffix names resolve only on a local network segment.
  if (host.endsWith(".local") || host.endsWith(".localdomain") || host.endsWith(".internal")) {
    return "resolves only on a local network, not from the public internet";
  }
  // A bare label ("backend", "api") is a container/compose hostname, never public.
  if (!host.includes(".") && !host.startsWith("[")) {
    return "is a single-label host (a container or LAN name), not a public domain";
  }

  const octets = parseIPv4(host);
  if (octets) {
    const [a, b] = octets;
    if (a === 127) return "is in the 127.0.0.0/8 loopback range";
    if (a === 10) return "is in the 10.0.0.0/8 private range";
    if (a === 172 && b >= 16 && b <= 31) return "is in the 172.16.0.0/12 private range";
    if (a === 192 && b === 168) return "is in the 192.168.0.0/16 private range";
    if (a === 169 && b === 254) return "is in the 169.254.0.0/16 link-local range";
    if (a === 100 && b >= 64 && b <= 127) return "is in the 100.64.0.0/10 carrier-grade NAT range";
    if (a === 0) return "is in the 0.0.0.0/8 unspecified range";
    // A public literal IP is left to the https requirement below, which it
    // effectively cannot satisfy in practice.
    return null;
  }

  if (host.startsWith("[")) {
    const inner = host.slice(1, -1);
    if (inner === "::1" || inner === "::") return "is an IPv6 loopback/unspecified address";
    if (/^f[cd]/.test(inner)) return "is in the fc00::/7 unique-local range";
    if (/^fe[89ab]/.test(inner)) return "is in the fe80::/10 link-local range";
    return null;
  }

  return null;
}

/**
 * Validate one candidate backend origin.
 *
 * @param {string | undefined} value
 * @returns {{ ok: true, origin: string } | { ok: false, reason: string }}
 */
export function classifyApiOrigin(value) {
  if (value === undefined || value === null || String(value).trim() === "") {
    return {
      ok: false,
      reason:
        "is not set. Next.js inlines NEXT_PUBLIC_* at build time, so every caller " +
        "would fall back to http://localhost:8000 in the shipped bundle.",
    };
  }

  const raw = String(value).trim();
  let url;
  try {
    url = new URL(raw);
  } catch {
    return { ok: false, reason: `is not a valid absolute URL: ${JSON.stringify(raw)}` };
  }

  if (url.protocol !== "https:" && url.protocol !== "http:") {
    return { ok: false, reason: `uses the ${url.protocol} scheme; expected https:` };
  }
  if (url.username || url.password) {
    return { ok: false, reason: "embeds credentials in the URL" };
  }
  // Callers concatenate directly -- `${API_URL}/api/v1/...` -- so anything past
  // the origin produces a malformed path ("//api/v1", "/base/api/v1").
  if (url.pathname !== "/" || raw.endsWith("/")) {
    return {
      ok: false,
      reason:
        `must be a bare origin with no trailing slash or path (got ${JSON.stringify(raw)}); ` +
        "callers build request URLs by direct concatenation",
    };
  }
  if (url.search || url.hash) {
    return { ok: false, reason: "must not carry a query string or fragment" };
  }

  const hostProblem = rejectHostname(url.hostname);
  if (hostProblem) {
    return { ok: false, reason: `points at a host that ${hostProblem}` };
  }

  if (url.protocol !== "https:") {
    return {
      ok: false,
      reason:
        "uses plain http. The production frontend is served over https, and a " +
        "browser blocks mixed-content requests before the page ever sees them.",
    };
  }

  return { ok: true, origin: url.origin };
}

/**
 * Check a whole build environment.
 *
 * @param {Record<string, string | undefined>} env
 * @returns {string[]} one message per problem; empty when the environment is sound
 */
export function checkBuildEnvironment(env) {
  const problems = [];
  for (const name of REQUIRED_API_ORIGIN_VARS) {
    const result = classifyApiOrigin(env[name]);
    if (!result.ok) problems.push(`${name} ${result.reason}`);
  }
  return problems;
}

function main() {
  const problems = checkBuildEnvironment(process.env);
  if (problems.length === 0) {
    // Record in the build log what was actually inlined.
    const checked = classifyApiOrigin(process.env.NEXT_PUBLIC_API_URL);
    console.log(`Backend API origin OK: ${checked.ok ? checked.origin : "?"}`);
    return;
  }

  console.error("Refusing to build a production frontend with this backend API origin:\n");
  for (const problem of problems) console.error(`  - ${problem}`);
  console.error(
    "\nNEXT_PUBLIC_* values are inlined into the client bundle at build time and " +
      "cannot be changed without rebuilding. Pass a public https origin (for example " +
      "https://vantacut-backend.onrender.com) as the NEXT_PUBLIC_API_URL build " +
      "argument and rebuild.",
  );
  process.exit(1);
}

// Only run when invoked directly, so the tests can import the pure parts.
// `pathToFileURL` rather than string-building a file: URL, so this also holds on
// Windows, where argv[1] is a drive-letter path.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
