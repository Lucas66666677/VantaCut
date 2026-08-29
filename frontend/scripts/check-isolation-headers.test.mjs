/**
 * Tests for the cross-origin isolation contract check.
 *
 * These run against fabricated manifests -- `node --test` must stay fast and must
 * not require a `next build` to have happened. The one test that does read the
 * real build output skips itself when `.next/routes-manifest.json` is absent, so
 * `npm run test:config` works on a clean checkout; CI's frontend-build job runs
 * `node scripts/check-isolation-headers.mjs` directly after its build, which is
 * where the real manifest is actually enforced.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

import {
  ARBITRARY_NESTED_PATH,
  DOCUMENT_PATHS,
  ISOLATION_HEADERS,
  RESOURCE_POLICY_HEADERS,
  ROUTES_MANIFEST,
  SUBRESOURCE_PATHS,
  checkIsolationHeaders,
} from "./check-isolation-headers.mjs";

/** The rule next.config.mjs currently produces: `source: "/:path*"`, compiled by next build. */
const EVERY_PATH_REGEX = "^(?:/((?:[^/]+?)(?:/(?:[^/]+?))*))?(?:/)?$";

function headerList(values) {
  return Object.entries(values).map(([key, value]) => ({ key, value }));
}

function manifestWith(overrides = {}) {
  return {
    headers: [
      {
        source: "/:path*",
        regex: EVERY_PATH_REGEX,
        headers: headerList({ ...ISOLATION_HEADERS, ...RESOURCE_POLICY_HEADERS }),
        ...overrides,
      },
    ],
  };
}

test("the shipped rule shape satisfies the contract", () => {
  assert.deepEqual(checkIsolationHeaders(manifestWith()), []);
});

test("header names are matched case-insensitively, as HTTP defines them", () => {
  const manifest = manifestWith();
  manifest.headers[0].headers = [
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
    { key: "CROSS-ORIGIN-EMBEDDER-POLICY", value: "REQUIRE-CORP" },
    { key: "cross-origin-resource-policy", value: " same-origin " },
  ];
  assert.deepEqual(checkIsolationHeaders(manifest), []);
});

test("a manifest with no header rules at all fails", () => {
  // What `headers()` being dropped from next.config.mjs looks like in the build output.
  const problems = checkIsolationHeaders({ headers: [] });
  assert.ok(problems.length > 0);
  assert.ok(problems.some((problem) => problem.startsWith("/ is missing")));
});

test("a manifest missing the headers key entirely fails rather than throwing", () => {
  assert.ok(checkIsolationHeaders({}).length > 0);
});

for (const dropped of Object.keys(ISOLATION_HEADERS)) {
  test(`dropping ${dropped} fails on every document path`, () => {
    const manifest = manifestWith();
    manifest.headers[0].headers = manifest.headers[0].headers.filter(
      (header) => header.key.toLowerCase() !== dropped,
    );
    const problems = checkIsolationHeaders(manifest);
    for (const path of DOCUMENT_PATHS) {
      assert.ok(
        problems.some((problem) => problem.startsWith(`${path} is `) && problem.includes(dropped)),
        `expected ${path} to be reported for ${dropped}`,
      );
    }
  });
}

test("weakening Cross-Origin-Embedder-Policy to credentialless fails", () => {
  // A plausible edit -- it relaxes subresource rules -- and it still isolates in
  // Chromium but not in every engine, so it is not a silent equivalent.
  const manifest = manifestWith();
  manifest.headers[0].headers = headerList({
    ...ISOLATION_HEADERS,
    ...RESOURCE_POLICY_HEADERS,
    "cross-origin-embedder-policy": "credentialless",
  });
  assert.ok(checkIsolationHeaders(manifest).some((problem) => problem.includes("require-corp")));
});

test("narrowing the rule to the document routes leaves the subresources uncovered", () => {
  // The header names are all still there; only the match narrowed. A source-string
  // comparison would have to enumerate every acceptable spelling to catch this.
  const manifest = manifestWith({ source: "/studio", regex: "^/studio(?:/)?$" });
  const problems = checkIsolationHeaders(manifest);
  for (const path of SUBRESOURCE_PATHS) {
    assert.ok(problems.some((problem) => problem.startsWith(`${path} is `)), `expected ${path} to be reported`);
  }
});

test("a rule matching only the known routes fails on an unenumerated future route", () => {
  // `.` is the only regex metacharacter these paths contain; `[.]` escapes it without a backslash.
  const known = [...DOCUMENT_PATHS, ...SUBRESOURCE_PATHS].map((path) => path.split(".").join("[.]"));
  const manifest = manifestWith({ source: "(known routes)", regex: `^(?:${known.join("|")})$` });
  const problems = checkIsolationHeaders(manifest);
  assert.deepEqual(
    problems.map((problem) => problem.split(" is ")[0]),
    Object.keys({ ...ISOLATION_HEADERS, ...RESOURCE_POLICY_HEADERS }).map(() => ARBITRARY_NESTED_PATH),
  );
});

test("a conditional rule does not count as an unconditional guarantee", () => {
  // `has`/`missing` make the rule apply only to requests carrying (or lacking) a
  // given header, cookie or query value -- a browser navigation generally does not.
  for (const condition of ["has", "missing"]) {
    const manifest = manifestWith({ [condition]: [{ type: "header", key: "x-isolate" }] });
    assert.ok(
      checkIsolationHeaders(manifest).length > 0,
      `expected a rule gated on \`${condition}\` to be rejected`,
    );
  }
});

test("two partial rules that together cover a path are accepted", () => {
  // Splitting the declaration is a legitimate refactor; only the served result matters.
  const manifest = {
    headers: [
      { source: "/:path*", regex: EVERY_PATH_REGEX, headers: headerList(ISOLATION_HEADERS) },
      { source: "/:path*", regex: EVERY_PATH_REGEX, headers: headerList(RESOURCE_POLICY_HEADERS) },
    ],
  };
  assert.deepEqual(checkIsolationHeaders(manifest), []);
});

test("the real build output, when one exists, satisfies the contract", (t) => {
  if (!existsSync(ROUTES_MANIFEST)) {
    t.skip("no .next/routes-manifest.json; run npm run build");
    return;
  }
  assert.deepEqual(checkIsolationHeaders(JSON.parse(readFileSync(ROUTES_MANIFEST, "utf8"))), []);
});
