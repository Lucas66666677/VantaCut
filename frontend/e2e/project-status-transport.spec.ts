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
 * deterministic and has no dependency on real network timing.
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

    await expect(page.getByTestId("harness-status")).toContainText('"progress":42');
    await expect(page.getByTestId("harness-connected")).toHaveText("true");
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
    await page.route("**/api/v1/projects/**/status", async (route) => {
      // Hold the response open well past when the test navigates away, so
      // an unaborted request would still be pending when we check.
      await new Promise((resolve) => setTimeout(resolve, 5_000));
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: status\ndata: {}\n\n" });
    });

    const abortedUrls: string[] = [];
    page.on("requestfailed", (request) => {
      if (request.url().includes("/status") && request.failure()?.errorText.includes("ABORTED")) {
        abortedUrls.push(request.url());
      }
    });

    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=sse`);
    await page.waitForTimeout(200); // let the fetch actually start
    await page.goto("about:blank");
    await page.waitForTimeout(500);

    expect(abortedUrls.length).toBeGreaterThan(0);
  });
});

test.describe("WebSocket transport (bearer subprotocol)", () => {
  async function installMockWebSocket(page: Page): Promise<void> {
    await page.addInitScript(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const w = window as any;
      w.__wsCalls = [];
      w.__wsInstances = [];
      class MockWebSocket {
        url: string;
        protocols: unknown;
        readyState = 0;
        onopen: ((event: unknown) => void) | null = null;
        onmessage: ((event: { data: string }) => void) | null = null;
        onclose: ((event: { code: number }) => void) | null = null;

        constructor(url: string, protocols: unknown) {
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

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const calls = await page.evaluate(() => (window as any).__wsCalls);
    expect(calls.length).toBeGreaterThanOrEqual(1);
    expect(calls[0].protocols).toEqual(["bearer", TEST_TOKEN]);
    expect(calls[0].url).not.toContain(TEST_TOKEN);
  });

  test("an authenticated status message is processed once the socket opens", async ({ page }) => {
    await installMockWebSocket(page);
    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=websocket`);

    await page.evaluate(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const ws = (window as any).__wsInstances[0];
      ws.onopen?.({});
      ws.onmessage?.({ data: JSON.stringify({ project_id: "p", progress: 77, stage: "render", status: "processing" }) });
    });

    await expect(page.getByTestId("harness-status")).toContainText('"progress":77');
  });

  test("a 1008 close (auth/ownership rejection) does not trigger an uncontrolled reconnect loop", async ({ page }) => {
    await installMockWebSocket(page);
    await signInAsAuthenticated(page);
    await page.goto(`/test-harness/project-status?projectId=${PROJECT_ID}&transport=websocket`);

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

    await page.evaluate(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const ws = (window as any).__wsInstances[0];
      ws.onclose?.({ code: 1006 });
    });

    await expect.poll(async () => page.evaluate(() => (window as any).__wsCalls.length), { timeout: 5_000 }).toBeGreaterThanOrEqual(2);
  });
});
