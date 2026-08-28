/**
 * Tests for the served-response cross-origin isolation check.
 *
 * These never touch the network. `probeOrigin` takes a `fetchImpl`, so the
 * responses below are fabricated -- including the one this check exists for: an
 * edge response carrying only `Cache-Control` and `Server: cloudflare`, which a
 * build-output assertion cannot see and which must fail here.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_ORIGIN,
  ISOLATION_AND_RESOURCE,
  PROBES,
  RESOURCE_POLICY,
  evaluateProbe,
  evaluateProbes,
  isApplicationResponse,
  normalizeHeaders,
  probeOrigin,
  requestUrl,
} from "./check-served-isolation-headers.mjs";

/** Headers a correctly served Next.js response on Render carries, as observed on the live origin. */
const SERVED_BY_THE_APP = {
  "Content-Type": "text/html; charset=utf-8",
  "Cross-Origin-Embedder-Policy": "require-corp",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Resource-Policy": "same-origin",
  "X-Powered-By": "Next.js",
  "X-Render-Origin-Server": "Render",
  Server: "cloudflare",
};

/** What the origin returns when the request never reaches the application. */
const SERVED_BY_THE_EDGE = {
  "Cache-Control": "s-maxage=31536000",
  Server: "cloudflare",
};

function response(status, headers) {
  return { status, headers: new Headers(headers) };
}

function fetchReturning(byPath) {
  return async (url, init) => {
    const { pathname } = new URL(url);
    const canned = typeof byPath === "function" ? byPath(pathname, init) : byPath;
    if (canned instanceof Error) throw canned;
    return { status: canned.status, headers: canned.headers, body: null };
  };
}

const neverSleep = async () => {};

test("a fully correct origin passes every probe", async () => {
  const results = await probeOrigin(DEFAULT_ORIGIN, {
    fetchImpl: fetchReturning(response(200, SERVED_BY_THE_APP)),
    sleep: neverSleep,
  });
  assert.deepEqual(evaluateProbes(results), []);
});

test("the failure this check exists for: an edge response with no isolation headers", async () => {
  // Exactly the state an independent check of the public host reported -- only
  // Cache-Control and Server: cloudflare. The build output is untouched and a
  // routes-manifest assertion still passes; this must not.
  const results = await probeOrigin(DEFAULT_ORIGIN, {
    fetchImpl: fetchReturning(response(200, SERVED_BY_THE_EDGE)),
    sleep: neverSleep,
  });
  const problems = evaluateProbes(results);
  assert.ok(problems.length > 0, "an edge response without the headers must fail");
  for (const probe of PROBES) {
    assert.ok(
      problems.some((problem) => problem.startsWith(`${probe.method} ${probe.path}:`)),
      `expected ${probe.path} to be reported`,
    );
  }
});

test("dropping only COEP fails, because the isolation pair is what grants SharedArrayBuffer", async () => {
  const withoutCoep = { ...SERVED_BY_THE_APP };
  delete withoutCoep["Cross-Origin-Embedder-Policy"];
  const results = await probeOrigin(DEFAULT_ORIGIN, {
    fetchImpl: fetchReturning(response(200, withoutCoep)),
    sleep: neverSleep,
  });
  const problems = evaluateProbes(results);
  assert.ok(problems.length > 0);
  assert.ok(problems.every((problem) => problem.includes("cross-origin-embedder-policy")));
});

test("a proxy that rewrites COOP to a weaker value fails", async () => {
  const results = await probeOrigin(DEFAULT_ORIGIN, {
    fetchImpl: fetchReturning(
      response(200, { ...SERVED_BY_THE_APP, "Cross-Origin-Opener-Policy": "same-origin-allow-popups" }),
    ),
    sleep: neverSleep,
  });
  const problems = evaluateProbes(results);
  assert.ok(problems.some((problem) => problem.includes("got same-origin-allow-popups")));
});

test("headers correct on the landing page but stripped elsewhere still fails", async () => {
  // Isolation is decided per document; /studio cannot inherit it from /.
  const results = await probeOrigin(DEFAULT_ORIGIN, {
    fetchImpl: fetchReturning((pathname) =>
      response(200, pathname === "/" ? SERVED_BY_THE_APP : SERVED_BY_THE_EDGE),
    ),
    sleep: neverSleep,
  });
  const problems = evaluateProbes(results);
  assert.ok(problems.some((problem) => problem.startsWith("GET /studio:")));
  assert.ok(!problems.some((problem) => problem.startsWith("GET /:")));
});

test("the deliberate 404 document must carry the isolation pair, and 404 is not itself a failure", () => {
  const notFound = PROBES.find((probe) => probe.status === "served");
  assert.ok(notFound, "expected one probe that only requires the application to answer");
  assert.deepEqual(evaluateProbe(notFound, response(404, SERVED_BY_THE_APP)), []);
  assert.ok(evaluateProbe(notFound, response(404, SERVED_BY_THE_EDGE)).length > 0);
});

test("an enumerated asset that 404s fails even when the header is present", () => {
  // A CORP header on a payload that is not there does not make the payload loadable.
  const asset = PROBES.find((probe) => probe.method === "HEAD");
  assert.deepEqual(evaluateProbe(asset, response(200, SERVED_BY_THE_APP)), []);
  const problems = evaluateProbe(asset, response(404, SERVED_BY_THE_APP));
  assert.ok(problems.some((problem) => problem.includes("expected 200, got 404")));
});

test("an origin that never answers fails closed rather than skipping", async () => {
  const results = await probeOrigin(DEFAULT_ORIGIN, {
    fetchImpl: fetchReturning(new Error("fetch failed")),
    attempts: 2,
    sleep: neverSleep,
  });
  const problems = evaluateProbes(results);
  assert.equal(problems.length, PROBES.length);
  assert.ok(problems.every((problem) => problem.includes("no response from the origin")));
});

test("a cold start is retried, then accepted once the application answers", async () => {
  let calls = 0;
  const results = await probeOrigin(DEFAULT_ORIGIN, {
    fetchImpl: async () => {
      calls += 1;
      const canned = calls === 1 ? response(502, SERVED_BY_THE_EDGE) : response(200, SERVED_BY_THE_APP);
      return { status: canned.status, headers: canned.headers, body: null };
    },
    sleep: neverSleep,
  });
  assert.deepEqual(evaluateProbes(results), []);
  assert.equal(calls, PROBES.length + 1, "only the 5xx should have been retried");
});

test("retries do not turn a persistent 5xx into a pass", async () => {
  const results = await probeOrigin(DEFAULT_ORIGIN, {
    fetchImpl: fetchReturning(response(503, SERVED_BY_THE_EDGE)),
    attempts: 2,
    sleep: neverSleep,
  });
  assert.ok(evaluateProbes(results).length > 0);
});

test("header names and values are compared the way HTTP defines them", () => {
  const shouting = {
    "CROSS-ORIGIN-OPENER-POLICY": "Same-Origin",
    "Cross-Origin-Embedder-Policy": " require-corp ",
    "cross-origin-resource-policy": "SAME-ORIGIN",
  };
  assert.deepEqual(evaluateProbe(PROBES[0], response(200, shouting)), []);
  assert.equal(normalizeHeaders({ "X-A": " B " }).get("x-a"), "b");
  assert.equal(normalizeHeaders(undefined).size, 0);
});

test("application markers are recognised, and their absence is what an edge response looks like", () => {
  assert.equal(isApplicationResponse(new Headers(SERVED_BY_THE_APP)), true);
  assert.equal(isApplicationResponse(new Headers(SERVED_BY_THE_EDGE)), false);
});

test("only a bare https origin can be probed", () => {
  assert.equal(requestUrl("https://example.com", "/studio"), "https://example.com/studio");
  assert.throws(() => requestUrl("http://example.com", "/"), /must be https/);
  assert.throws(() => requestUrl("https://example.com/app", "/"), /bare origin/);
});

test("every document probe requires the full isolation pair, every asset probe the resource policy", () => {
  const documents = PROBES.filter((probe) => probe.method === "GET");
  const assets = PROBES.filter((probe) => probe.method === "HEAD");
  assert.ok(documents.length >= 5 && assets.length >= 3);
  for (const probe of documents) assert.deepEqual(probe.requires, ISOLATION_AND_RESOURCE);
  for (const probe of assets) assert.deepEqual(probe.requires, RESOURCE_POLICY);
});

test("the default origin is the one render.yaml publishes the frontend on", () => {
  // Keeps this in step with the blueprint: renaming the service breaks the test
  // rather than silently pointing the check at a host nothing deploys to.
  const blueprint = readFileSync(fileURLToPath(new URL("../../render.yaml", import.meta.url)), "utf8");
  const { host, protocol, pathname } = new URL(DEFAULT_ORIGIN);
  assert.equal(protocol, "https:");
  assert.equal(pathname, "/");
  const [service] = host.split(".");
  assert.ok(
    new RegExp(`^\\s+name:\\s*${service}\\s*$`, "m").test(blueprint),
    `render.yaml declares no service named ${service}; DEFAULT_ORIGIN is stale`,
  );
});
