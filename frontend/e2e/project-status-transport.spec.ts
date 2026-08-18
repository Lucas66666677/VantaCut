import { expect, test, type Page } from "@playwright/test";

/**
 * Focused security-regression coverage for the SSE/WebSocket transport
 * rewrite in features/project-status/use-project-status.ts, driven through
 * the real AuthGate/auth-store integration (not a bypass of it) against the
 * Playwright-only harness page at app/test-harness/project-status —
 * see that file for why a harness page exists at all.
 *
 * The WebSocket half is tested with a small in-page mock WebSocket class
 * (installed via addInitScript before any app code runs) rather than a real
 * socket or a WebSocket-routing API, so every assertion — the exact
 * constructor arguments, message delivery, and close-code handling — is
 * deterministic and has no dependency on real network timing. The mock only
 * intercepts connections to the app's own project-status endpoint
 * (URLs containing "/status/ws") — Next dev's own HMR client also opens a
 * WebSocket (to /_next/webpack-hmr) on every page load, and since
 * addInitScript replaces the global constructor before ANY page script
 * runs, an unfiltered mock captures that connection too, as __wsCalls[0]/
 * __wsInstances[0] instead of the app's real one (confirmed via a
 * temporary CI diagnostic — this was silently corrupting every assertion
 * that indexed into these arrays).
 */

const TOKEN_STORAGE_KEY = "vantacut_access_token";
const TEST_TOKEN = "test-access-token-xyz789";
const TEST_USER = { id: "22222222-2222-2222-2222-222222222222", email: "reviewer@example.com", display_name: null, is_active: true };
const PROJECT_ID = "33333333-3333-3333-3333-333333333333";

async function signInAsAuthenticated(page: Page): Promise<void> {
  await page.route("**/api/v1/auth/me", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TEST_USER) });
  });
  await page.goto("/test-harness/project-status");
  await page.evaluate((args) => window.sessionStorage.setItem(args.key, args.token), { key: TOKEN_STORAGE_KEY, token: TEST_TOKEN });
}

test.describe("SSE transport (authenticated fetch-stream)", () => {
  test("authenticated request carries the Authorization header and no token in the URL", async ({ page }) => {
    let seenAuthHeader: string | undefined;
    let seenUrl = "";
    await page.route("**/api/v1/projects/**/status", async (route) => {
      seenAuthHeader = route.request().headers()["authorization"];
      seenUrl = route.request().url();
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: status\ndata: {\"project_id\":\"p\",\"progress\":0,\"stage\":\"idle\",\"status\":\"idle\"}\n\n" });
    });

    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=sse`);

    await expect.poll(() => seenAuthHeader).toBe(`Bearer ${TEST_TOKEN}`);
    expect(seenUrl).not.toContain(TEST_TOKEN);
  });

  test("an SSE status event is parsed and reflected in app state", async ({ page }) => {
    await page.route("**/api/v1/projects/**/status", async (route) => {
      const payload = JSON.stringify({ project_id: PROJECT_ID, progress: 42, stage: "render", status: "processing" });
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: `event: status\ndata: ${payload}\n\n` });
    });

    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=sse`);

    // page.route().fulfill() always delivers a single, complete,
    // immediately-closing response body — it cannot represent the real
    // backend's held-open, keepalive'd stream (see use-project-status.ts's
    // doc comment on consumeEventStream). So right after processing this
    // one event, the app correctly — by design, see connectSse's "stream
    // ended" handling — treats the now-closed body as a dropped connection
    // and flips `connected` back to false while scheduling a reconnect,
    // exactly as it should for a real disconnect. That makes the
    // `connected: true` window here transient rather than a value the app
    // settles into, so this polls at waitForFunction's default
    // (requestAnimationFrame-driven, much tighter than a normal assertion's
    // retry interval) granularity to reliably observe it, instead of two
    // sequential toHaveText()/toContainText() checks that can each land
    // after it has already flipped back.
    // waitForFunction's signature is (pageFunction, arg, options) — the
    // callback takes no argument, but that middle positional slot must
    // still be filled (with undefined) for the timeout override in the
    // third argument to actually apply; passing the options object as the
    // second argument silently discards it as an unused `arg` and falls
    // through to the test's full default timeout instead.
    await page.waitForFunction(
      () => {
        const status = document.querySelector('[data-testid="harness-status"]')?.textContent ?? "";
        const connected = document.querySelector('[data-testid="harness-connected"]')?.textContent ?? "";
        return status.includes('"progress":42') && connected === "true";
      },
      undefined,
      { timeout: 5_000 },
    );
  });

  test("a 401 from the status stream does not enter an uncontrolled reconnect loop", async ({ page }) => {
    let requestCount = 0;
    await page.route("**/api/v1/projects/**/status", async (route) => {
      requestCount += 1;
      await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Could not validate credentials" }) });
    });

    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=sse`);

    await expect.poll(() => requestCount).toBeGreaterThanOrEqual(1);
    // Give the 1s/2s/4s... backoff schedule ample time to have fired at
    // least once more if the (buggy) behavior were "reconnect on 401".
    await page.waitForTimeout(3_000);
    expect(requestCount).toBe(1);
    // A 401 also means the whole session is invalid, not just this stream —
    // the frontend should have dropped it.
    expect(await page.evaluate((key) => window.sessionStorage.getItem(key), TOKEN_STORAGE_KEY)).toBeNull();
  });

  test("navigating away aborts the in-flight stream request", async ({ page }) => {
    let requestStarted = false;
    await page.route("**/api/v1/projects/**/status", async (route) => {
      requestStarted = true;
      // Hold the response open well past when the test navigates away, so
      // an unaborted request would still be pending when we check.
      await new Promise((resolve) => setTimeout(resolve, 5_000));
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: status\ndata: {}\n\n" });
    });

    // Match any failed request for this URL, not just ones whose errorText
    // contains "ABORTED": Chromium's reported reason for a request torn
    // down by cross-document navigation isn't guaranteed to be that exact
    // string, and the thing actually under test is narrower and more
    // important than the specific wording — that the request doesn't
    // silently outlive the navigation.
    const failedUrls: string[] = [];
    page.on("requestfailed", (request) => {
      if (request.url().includes("/status")) {
        failedUrls.push(request.url());
      }
    });

    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=sse`);
    // page.goto() resolves once navigation/load completes, not once the
    // app's async auth-restore-then-connect chain has actually reached the
    // point of issuing the fetch — so this polls for the request to have
    // genuinely started rather than guessing with a fixed timeout (see the
    // identical race in the WebSocket tests below).
    await expect.poll(() => requestStarted).toBe(true);
    await page.goto("about:blank");

    // Give the requestfailed event more room to surface than a flat 500ms:
    // it has to round-trip through CDP for a request tied to a document
    // that cross-navigation is simultaneously tearing down, which is a
    // slower, less deterministic path than an ordinary same-page failure.
    await expect.poll(() => failedUrls.length, { timeout: 3_000 }).toBeGreaterThan(0);
  });
});

test.describe("WebSocket transport (bearer subprotocol)", () => {
  async function installMockWebSocket(page: Page): Promise<void> {
    await page.addInitScript(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const w = window as any;
      w.__wsCalls = [];
      w.__wsInstances = [];
      const RealWebSocket = window.WebSocket;
      class MockWebSocket {
        url!: string;
        protocols: unknown;
        readyState = 0;
        onopen: ((event: unknown) => void) | null = null;
        onmessage: ((event: { data: string }) => void) | null = null;
        onclose: ((event: { code: number }) => void) | null = null;

        constructor(url: string, protocols?: unknown) {
          // Only the app's own project-status connection is mocked. Next
          // dev's own HMR client also constructs a WebSocket (to
          // /_next/webpack-hmr) on every page load, and since this global
          // override applies before any page script runs — including
          // Next's — an unfiltered mock captures that connection too,
          // ending up as index 0 instead of the app's real one. Anything
          // that isn't the project-status endpoint falls through to the
          // real WebSocket untouched.
          if (!url.includes("/status/ws")) {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            return new RealWebSocket(url, protocols as any) as unknown as MockWebSocket;
          }
          this.url = url;
          this.protocols = protocols;
          w.__wsCalls.push({ url, protocols });
          w.__wsInstances.push(this);
        }
        close() {
          this.readyState = 3;
        }
        send() {
          /* not used by this hook */
        }
      }
      w.WebSocket = MockWebSocket;
    });
  }

  test("WebSocket is constructed with the bearer subprotocol and no token in the URL", async ({ page }) => {
    await installMockWebSocket(page);
    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=websocket`);

    // page.goto() resolves once navigation/load completes, not once the
    // app's async auth-restore-then-connect chain has actually reached the
    // point of constructing the WebSocket — so this polls rather than
    // reading __wsCalls immediately.
    await expect.poll(async () => page.evaluate(() => (window as any).__wsCalls.length)).toBeGreaterThanOrEqual(1);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const calls = await page.evaluate(() => (window as any).__wsCalls);
    expect(calls[0].protocols).toEqual(["bearer", TEST_TOKEN]);
    expect(calls[0].url).not.toContain(TEST_TOKEN);
  });

  test("an authenticated status message is processed once the socket opens", async ({ page }) => {
    await installMockWebSocket(page);
    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=websocket`);

    await expect.poll(async () => page.evaluate(() => (window as any).__wsInstances.length)).toBeGreaterThanOrEqual(1);
    await page.evaluate(
      // The message payload's project_id must match the real PROJECT_ID the
      // harness is subscribed to — project-status-store.ts's setProjectStatus
      // keys the store by `status.project_id`, so a mismatched id (this used
      // to be the placeholder "p") silently updates an unrelated store entry
      // that the harness never reads, instead of the one it renders.
      (projectId) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const ws = (window as any).__wsInstances[0];
        ws.onopen?.({});
        ws.onmessage?.({ data: JSON.stringify({ project_id: projectId, progress: 77, stage: "render", status: "processing" }) });
      },
      PROJECT_ID,
    );

    await expect(page.getByTestId("harness-status")).toContainText('"progress":77');
  });

  test("a 1008 close (auth/ownership rejection) does not trigger an uncontrolled reconnect loop", async ({ page }) => {
    await installMockWebSocket(page);
    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=websocket`);

    await expect.poll(async () => page.evaluate(() => (window as any).__wsInstances.length)).toBeGreaterThanOrEqual(1);
    await page.evaluate(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const ws = (window as any).__wsInstances[0];
      ws.onclose?.({ code: 1008 });
    });

    // Give the backoff schedule ample time to have opened a second socket
    // if the (buggy) behavior were "always reconnect".
    await page.waitForTimeout(3_000);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const calls = await page.evaluate(() => (window as any).__wsCalls);
    expect(calls.length).toBe(1);
  });

  test("a non-auth close (e.g. transient 1006) does reconnect", async ({ page }) => {
    await installMockWebSocket(page);
    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=websocket`);

    await expect.poll(async () => page.evaluate(() => (window as any).__wsInstances.length)).toBeGreaterThanOrEqual(1);
    await page.evaluate(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const ws = (window as any).__wsInstances[0];
      ws.onclose?.({ code: 1006 });
    });

    await expect.poll(async () => page.evaluate(() => (window as any).__wsCalls.length), { timeout: 5_000 }).toBeGreaterThanOrEqual(2);
  });
});
