/**
 * Tests for the production backend-origin guard.
 *
 * Uses `node --test`, which is built into Node 22 -- the version every
 * Dockerfile and every CI job here already pins -- so this adds no dependency
 * to a frontend that otherwise has only Playwright.
 *
 *     npm run test:config
 *
 * The rejection cases below are the ones that actually reach a deploy: an
 * unset variable (the default state of a freshly created Render service), the
 * `http://localhost:8000` that every caller falls back to, a compose hostname,
 * a private LAN address left over from a staging config, and a trailing slash.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  REQUIRED_API_ORIGIN_VARS,
  checkBuildEnvironment,
  classifyApiOrigin,
} from "./check-public-api-origin.mjs";

const GOOD = "https://vantacut-backend.onrender.com";

test("accepts the real production origin", () => {
  const result = classifyApiOrigin(GOOD);
  assert.equal(result.ok, true);
  assert.equal(result.origin, GOOD);
});

test("accepts a public origin with an explicit port and surrounding whitespace", () => {
  const result = classifyApiOrigin("  https://api.vantacut.example:8443  ");
  assert.equal(result.ok, true);
  assert.equal(result.origin, "https://api.vantacut.example:8443");
});

test("rejects an unset or blank value", () => {
  for (const value of [undefined, null, "", "   "]) {
    const result = classifyApiOrigin(value);
    assert.equal(result.ok, false, `expected ${JSON.stringify(value)} to be rejected`);
    assert.match(result.reason, /not set/);
  }
});

test("rejects the localhost fallback every caller would otherwise use", () => {
  const result = classifyApiOrigin("http://localhost:8000");
  assert.equal(result.ok, false);
  assert.match(result.reason, /loopback/);
});

test("rejects loopback addresses in every spelling", () => {
  const loopbacks = [
    "http://127.0.0.1:8000",
    "https://127.0.0.1",
    "http://127.1.2.3:8000",
    "http://0.0.0.0:3000",
    "https://[::1]:8000",
    "http://api.localhost:8000",
  ];
  for (const value of loopbacks) {
    const result = classifyApiOrigin(value);
    assert.equal(result.ok, false, `expected ${value} to be rejected`);
    assert.match(result.reason, /loopback|unspecified/);
  }
});

test("rejects private, link-local and CGNAT ranges", () => {
  const cases = [
    ["https://10.0.0.5", /10\.0\.0\.0\/8/],
    ["https://172.16.4.9", /172\.16\.0\.0\/12/],
    ["https://172.31.255.255", /172\.16\.0\.0\/12/],
    ["https://192.168.1.20", /192\.168\.0\.0\/16/],
    ["https://169.254.10.1", /169\.254\.0\.0\/16/],
    ["https://100.100.0.1", /100\.64\.0\.0\/10/],
    ["https://[fd12:3456::1]", /fc00::\/7/],
    ["https://[fe80::1]", /fe80::\/10/],
  ];
  for (const [value, expected] of cases) {
    const result = classifyApiOrigin(value);
    assert.equal(result.ok, false, `expected ${value} to be rejected`);
    assert.match(result.reason, expected);
  }
});

test("does not mistake public addresses adjacent to private ranges for private ones", () => {
  // 172.15/172.32 sit just outside 172.16.0.0/12; 11.x and 192.167.x are public.
  for (const value of ["https://172.15.0.1", "https://172.32.0.1", "https://11.0.0.1"]) {
    const result = classifyApiOrigin(value);
    assert.equal(result.ok, true, `expected ${value} to be accepted: ${result.reason ?? ""}`);
  }
});

test("rejects container and LAN-only hostnames", () => {
  const cases = [
    ["https://backend:8000", /single-label host/],
    ["https://macbook.local:8000", /local network/],
    ["https://api.internal", /local network/],
  ];
  for (const [value, expected] of cases) {
    const result = classifyApiOrigin(value);
    assert.equal(result.ok, false, `expected ${value} to be rejected`);
    assert.match(result.reason, expected);
  }
});

test("rejects values that are not absolute http(s) URLs", () => {
  for (const value of ["vantacut-backend.onrender.com", "not a url", "/api/v1"]) {
    const result = classifyApiOrigin(value);
    assert.equal(result.ok, false, `expected ${JSON.stringify(value)} to be rejected`);
    assert.match(result.reason, /not a valid absolute URL/);
  }
  const ftp = classifyApiOrigin("ftp://vantacut-backend.onrender.com");
  assert.equal(ftp.ok, false);
  assert.match(ftp.reason, /scheme/);
});

test("rejects a trailing slash or a path, which callers would concatenate onto", () => {
  for (const value of [`${GOOD}/`, `${GOOD}/api`, `${GOOD}/api/v1`]) {
    const result = classifyApiOrigin(value);
    assert.equal(result.ok, false, `expected ${value} to be rejected`);
    assert.match(result.reason, /bare origin/);
  }
});

test("rejects a query string, fragment, or embedded credentials", () => {
  assert.match(classifyApiOrigin(`${GOOD}?token=x`).reason, /query string|bare origin/);
  assert.match(classifyApiOrigin(`${GOOD}#x`).reason, /fragment|bare origin/);
  assert.match(
    classifyApiOrigin("https://user:pass@vantacut-backend.onrender.com").reason,
    /credentials/,
  );
});

test("rejects plain http to a public host, which the browser blocks as mixed content", () => {
  const result = classifyApiOrigin("http://vantacut-backend.onrender.com");
  assert.equal(result.ok, false);
  assert.match(result.reason, /mixed-content/);
});

test("checkBuildEnvironment passes only when every variable the source reads is sound", () => {
  const sound = Object.fromEntries(REQUIRED_API_ORIGIN_VARS.map((name) => [name, GOOD]));
  assert.deepEqual(checkBuildEnvironment(sound), []);
});

test("checkBuildEnvironment reports NEXT_PUBLIC_API_BASE_URL, not just NEXT_PUBLIC_API_URL", () => {
  // The regression this guard exists for: NEXT_PUBLIC_API_URL is configured
  // correctly, the build succeeds, and the twelve modules reading the other
  // name still ship http://localhost:8000.
  const problems = checkBuildEnvironment({ NEXT_PUBLIC_API_URL: GOOD });
  assert.equal(problems.length, 1);
  assert.match(problems[0], /^NEXT_PUBLIC_API_BASE_URL is not set/);
});

test("checkBuildEnvironment reports every offending variable at once", () => {
  const problems = checkBuildEnvironment({
    NEXT_PUBLIC_API_URL: "http://localhost:8000",
    NEXT_PUBLIC_API_BASE_URL: "http://192.168.0.10:8000",
  });
  assert.equal(problems.length, 2);
  assert.match(problems[0], /^NEXT_PUBLIC_API_URL .*loopback/);
  assert.match(problems[1], /^NEXT_PUBLIC_API_BASE_URL .*192\.168\.0\.0\/16/);
});

test("an empty environment is rejected for every required variable", () => {
  assert.equal(checkBuildEnvironment({}).length, REQUIRED_API_ORIGIN_VARS.length);
});
