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

    // Records every DOM mutation of the two harness testids with a
    // timestamp, and asserts against that recorded HISTORY rather than
    // polling live DOM state. This is not incidental — it's the actual
    // fix, arrived at via 3 rounds of CI evidence (runs #19-#21):
    //
    // Round 1 (run #19) used a one-shot page.evaluate() to install this
    // recorder, which produced `SNAP_DEBUG undefined`: the test's second
    // page.goto() below (the one with query params) is a full
    // cross-document navigation — a fresh `window` — so anything installed
    // beforehand via page.evaluate() is discarded before the activity
    // under test happens. Fixed by switching to page.addInitScript(),
    // which re-arms on every subsequent navigation of this page.
    //
    // Round 2 (run #20) observed `document.querySelector("main")`, which
    // matched the WRONG element: features/auth/auth-gate.tsx's
    // `status === "loading"` branch renders its own, unrelated
    // `<main>正在確認登入狀態…</main>` that exists before auth resolves.
    // AuthGate then unmounts/replaces that entire subtree once auth
    // resolves (swapping to {children} + LogoutControl), so the recorder,
    // bound to a now-detached node, never saw the harness's real content
    // (confirmed: one snapshot at t=37ms, both fields empty, then
    // silence). Fixed by observing `document.body` instead, which
    // persists across AuthGate's state transitions.
    //
    // Round 3 (run #21), with the recorder finally attached to the right
    // node, produced the real answer to the original question. The
    // history showed `connected:true` co-occurring with `"progress":42`
    // for real — e.g. `{"t":1077,...connected:true}` immediately followed
    // by `{"t":1078,...connected:false}`, a ~1ms window — repeating on
    // every ~1s reconnect cycle for the full 5s the test watched. The
    // state exists exactly as the app's design predicts (see the comment
    // below); what doesn't exist is any way for page.waitForFunction's
    // default requestAnimationFrame-driven polling to reliably catch a
    // window that narrow, because both the "connected:true" mutation and
    // the following "connected:false" mutation can — and, empirically,
    // consistently do — land within the same animation frame, before the
    // browser ever paints the intermediate state waitForFunction is
    // polling for. A MutationObserver callback, by contrast, runs as a
    // microtask right after each individual mutation, so it does catch
    // both states even when they're separated by no paint at all — which
    // is exactly why this recorder (not a live poll) is the correct tool
    // for asserting this, not just for debugging it.
    await page.addInitScript(() => {
      const w = window as any;
      w.__snap = [] as Array<{ t: number; status: string; connected: string }>;
      const start = performance.now();
      const record = () => {
        const status = document.querySelector('[data-testid="harness-status"]')?.textContent ?? "";
        const connected = document.querySelector('[data-testid="harness-connected"]')?.textContent ?? "";
        w.__snap.push({ t: Math.round(performance.now() - start), status, connected });
      };
      const attach = () => {
        if (document.body) {
          record();
          new MutationObserver(record).observe(document.body, { childList: true, characterData: true, subtree: true });
        } else {
          requestAnimationFrame(attach);
        }
      };
      attach();
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
    // exactly as it should for a real disconnect, then reconnects (and
    // re-receives the same mocked event) roughly every second. Wait for a
    // couple of those cycles so the recorder above has multiple chances to
    // capture the transient co-occurrence, then assert against the
    // recorded history instead of the live DOM.
    await expect(page.getByTestId("harness-status")).toContainText('"progress":42');
    await page.waitForTimeout(2_500);
    const snap = (await page.evaluate(() => (window as any).__snap)) as Array<{ status: string; connected: string }>;
    const sawConnectedWithProgress = snap.some((entry) => entry.status.includes('"progress":42') && entry.connected === "true");
    expect(sawConnectedWithProgress).toBe(true);
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

  test("an auth-state change (logout) aborts the in-flight stream request", async ({ page }) => {
    // This test used to simulate "navigating away" with page.goto("about:blank")
    // — a hard, cross-document navigation. Three rounds of CI evidence
    // (runs #19, #20, #21) tried three different ways to observe the
    // outcome of that (page.on("requestfailed"), page.on("requestfinished"),
    // and finally checking whether the mocked route handler's own
    // route.fulfill() call throws once the page is gone) and all three
    // came back empty or contradictory: no request-lifecycle event ever
    // fires after such a navigation, and route.fulfill() resolves
    // ("delivered") rather than throwing even when called well after the
    // page has navigated away. That's not just a missing signal to find a
    // fourth way around — it points at something more fundamental: a hard
    // cross-document navigation destroys the whole JS realm synchronously,
    // before React ever gets a chance to run this effect's cleanup
    // function (the one that calls `abortController.abort()` in
    // use-project-status.ts). So the original test was never actually
    // exercising the app's own cleanup code at all — whatever happens to
    // the in-flight request on a hard navigation is entirely the browser's
    // own standard behavior (navigating away cancels a discarded
    // document's pending fetches), independent of and unreachable by this
    // app's code, and not something Playwright's request-mocking layer
    // reports through any observable channel in this scenario.
    //
    // The thing actually worth testing — that THIS APP'S cleanup code
    // correctly aborts an in-flight status request rather than leaking it
    // — does run, and is reliably observable, when the effect's own
    // dependencies change within the SAME document. Logging out is the
    // simplest real, already-existing trigger for that: it clears
    // useAuthStore's `token` (see lib/auth/auth-store.ts's `logout`),
    // which is a dependency of the effect in use-project-status.ts, so its
    // cleanup runs (calling abortController.abort()) and, since `token` is
    // now null, the effect body returns early instead of reconnecting.
    // AuthGate also swaps HarnessContent out for AuthForm at the same
    // time, unmounting the harness entirely — a completely ordinary,
    // same-page teardown, which is exactly the case Playwright's request
    // events are well-supported for (every other test in this file relies
    // on them without issue).
    let requestStarted = false;
    const failedUrls: string[] = [];
    page.on("requestfailed", (request) => {
      if (request.url().includes("/status")) failedUrls.push(request.url());
    });
    await page.route("**/api/v1/projects/**/status", async (route) => {
      requestStarted = true;
      // Hold the response open well past when logout tears down the
      // component, so an unaborted request would still be pending when we
      // check. Same-page teardown doesn't have the observability problem
      // the old cross-navigation version did, so this can safely be long.
      await new Promise((resolve) => setTimeout(resolve, 5_000));
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: status\ndata: {}\n\n" }).catch(() => {
        // The request may already be gone by the time this resolves —
        // that's fine, requestStarted/failedUrls already captured what we
        // need. Swallow so this handler doesn't produce an unhandled
        // rejection in the test process.
      });
    });

    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=sse`);
    // page.goto() resolves once navigation/load completes, not once the
    // app's async auth-restore-then-connect chain has actually reached the
    // point of issuing the fetch — so this polls for the request to have
    // genuinely started rather than guessing with a fixed timeout (see the
    // identical race in the WebSocket tests below).
    await expect.poll(() => requestStarted).toBe(true);

    await page.getByRole("button", { name: "登出" }).click();
    // Confirms the logout itself actually completed (same signal
    // auth-foundation.spec.ts's logout test uses), so a failure here can't
    // be confused with the abort assertion below.
    await expect(page.getByLabel("電子郵件")).toBeVisible();

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
