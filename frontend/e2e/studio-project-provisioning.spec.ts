import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The studio must arrive at the editor holding a real project id.
 *
 * `LocalMediaBin` uploads only when it is given one, and the studio never was,
 * so every file stayed in the browser while the workspace promised background
 * cloud sync. The backend gap is closed by `POST /api/v1/projects`; this covers
 * the browser half.
 *
 * Network boundaries are mocked with `page.route()` — the provisioning logic
 * itself runs for real, the same way `auth-foundation.spec.ts` drives the auth
 * store. Nothing here reaches a deployment.
 */

const TOKEN_STORAGE_KEY = "vantacut_access_token";
const TEST_TOKEN = "studio-project-token";
const TEST_USER = { id: "22222222-2222-2222-2222-222222222222", email: "studio@example.com", display_name: null, is_active: true };
const EXISTING_PROJECT = { id: "33333333-3333-3333-3333-333333333333", name: "既有專案", description: null, lifecycle_state: "active", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" };
const CREATED_PROJECT = { ...EXISTING_PROJECT, id: "44444444-4444-4444-4444-444444444444", name: "未命名專案" };

type Recorded = { method: string; authorization: string | undefined };

/** A signed-in session, established before the app boots. */
async function signedIn(page: Page): Promise<void> {
  await page.addInitScript(
    ([key, token]) => window.sessionStorage.setItem(key, token),
    [TOKEN_STORAGE_KEY, TEST_TOKEN] as const,
  );
  await page.route("**/api/v1/auth/me", async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TEST_USER) });
  });
}

/** Serve the projects endpoint and record every call made to it. */
async function withProjects(page: Page, listed: unknown[], calls: Recorded[]): Promise<void> {
  await page.route("**/api/v1/projects**", async (route: Route) => {
    const request = route.request();
    calls.push({ method: request.method(), authorization: request.headers()["authorization"] });
    if (request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(listed) });
      return;
    }
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(CREATED_PROJECT) });
  });
}

test("a visitor with no project gets one created before uploading", async ({ page }) => {
  const calls: Recorded[] = [];
  await signedIn(page);
  await withProjects(page, [], calls);

  await page.goto("/studio");
  await expect(page.getByLabel("描述剪輯需求")).toBeVisible();

  // The listing comes first so a refresh cannot mint a second workspace, and
  // the create only happens because the listing came back empty. Settle before
  // counting: the launchpad is remounted once (Suspense + useSearchParams), so
  // a check that stopped at the first matching moment would miss a second
  // create arriving after it.
  await expect.poll(() => calls.filter((call) => call.method === "POST").length).toBe(1);
  await page.waitForTimeout(500);
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
  expect(calls[0].method).toBe("GET");
});

test("an existing project is reused rather than duplicated", async ({ page }) => {
  const calls: Recorded[] = [];
  await signedIn(page);
  await withProjects(page, [EXISTING_PROJECT], calls);

  await page.goto("/studio");
  await expect(page.getByLabel("描述剪輯需求")).toBeVisible();

  // Give any stray create a chance to fire before asserting it did not. The
  // GET count is deliberately not pinned -- the launchpad is remounted once,
  // and an extra idempotent listing is harmless. Creating a second project is
  // not, and that is what this asserts.
  await expect.poll(() => calls.length).toBeGreaterThan(0);
  await page.waitForTimeout(500);
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
});

test("provisioning carries the session and never puts the token in the URL", async ({ page }) => {
  const calls: Recorded[] = [];
  const urls: string[] = [];
  await signedIn(page);
  await page.route("**/api/v1/projects**", async (route: Route) => {
    urls.push(route.request().url());
    calls.push({ method: route.request().method(), authorization: route.request().headers()["authorization"] });
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([EXISTING_PROJECT]) });
  });

  await page.goto("/studio");
  await expect(page.getByLabel("描述剪輯需求")).toBeVisible();

  await expect.poll(() => calls.length).toBeGreaterThan(0);
  for (const call of calls) {
    expect(call.authorization).toBe(`Bearer ${TEST_TOKEN}`);
  }
  for (const url of urls) {
    expect(url).not.toContain(TEST_TOKEN);
  }
});

test("a provisioning failure leaves the editor usable rather than blank", async ({ page }) => {
  // Losing cloud sync is worse than losing the session; it must not become a
  // blank screen. The media bin already renders the local-only state.
  await signedIn(page);
  await page.route("**/api/v1/projects**", async (route: Route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "unavailable" }) });
  });

  await page.goto("/studio");
  await expect(page.getByLabel("描述剪輯需求")).toBeVisible();
  await expect(page.getByRole("button", { name: "套用工作區" })).toBeVisible();
});
