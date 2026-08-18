import { expect, test, type Page } from "@playwright/test";

/**
 * Focused security-regression coverage for the frontend auth foundation
 * (lib/auth/*, features/auth/*). Network boundaries are mocked via
 * page.route() — POST /auth/login, POST /auth/register, GET /auth/me — the
 * auth store/UI logic itself runs for real. See
 * e2e/project-status-transport.spec.ts for the SSE/WebSocket transport
 * coverage.
 *
 * Drives AuthGate through app/test-harness/auth (not /studio): /studio's
 * only content, features/onboarding/studio-launchpad.tsx, statically
 * imports features/workspace/adaptive-editor-workspace.tsx, which in turn
 * imports two modules that have never existed anywhere in this repo's git
 * history and were already broken on the base branch before this PR — so
 * /studio cannot compile in this environment (dev or production build)
 * regardless of anything AuthGate does. The harness route wraps the exact
 * same AuthGate/AuthForm/auth-store code with no dependency on that
 * unrelated, pre-existing, out-of-scope bug. See that harness page's doc
 * comment for the full explanation.
 */

const TOKEN_STORAGE_KEY = "vantacut_access_token";
const TEST_TOKEN = "test-access-token-abc123";
const TEST_USER = { id: "11111111-1111-1111-1111-111111111111", email: "reviewer@example.com", display_name: null, is_active: true };

async function readStoredToken(page: Page): Promise<string | null> {
  return page.evaluate((key) => window.sessionStorage.getItem(key), TOKEN_STORAGE_KEY);
}

test("successful login stores the access token in sessionStorage and reaches the app", async ({ page }) => {
  let loginBody: unknown;
  await page.route("**/api/v1/auth/login", async (route) => {
    loginBody = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ access_token: TEST_TOKEN, token_type: "bearer" }) });
  });
  await page.route("**/api/v1/auth/me", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TEST_USER) });
  });

  await page.goto("/test-harness/auth");
  await expect(page.getByLabel("電子郵件")).toBeVisible();

  await page.getByLabel("電子郵件").fill(TEST_USER.email);
  await page.getByLabel("密碼").fill("correct-horse-battery-staple");
  await page.getByRole("button", { name: "登入" }).click();

  // The auth form unmounts once AuthGate's status flips to "authenticated".
  await expect(page.getByLabel("電子郵件")).toBeHidden();
  await expect.poll(() => readStoredToken(page)).toBe(TEST_TOKEN);
  expect(loginBody).toEqual({ email: TEST_USER.email, password: "correct-horse-battery-staple" });
});

test("failed login does not store a token and shows a generic error", async ({ page }) => {
  await page.route("**/api/v1/auth/login", async (route) => {
    await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Invalid email or password" }) });
  });

  const consoleTexts: string[] = [];
  page.on("console", (message) => consoleTexts.push(message.text()));

  await page.goto("/test-harness/auth");
  await page.getByLabel("電子郵件").fill("nope@example.com");
  await page.getByLabel("密碼").fill("totally-wrong-password");
  await page.getByRole("button", { name: "登入" }).click();

  // Scoped by text, not just role: Next.js's App Router injects its own
  // <div role="alert" aria-live="assertive" id="__next-route-announcer__">
  // (a screen-reader route-change announcer) into every page, so a bare
  // getByRole("alert") matches two elements here and fails Playwright's
  // strict-mode check. That announcer element is normal, unrelated
  // Next.js framework behavior, not something this app renders — it was
  // only ever invisible to this test before because app/test-harness/auth
  // 404'd on every request (see that page's doc comment).
  await expect(page.getByRole("alert").filter({ hasText: "登入失敗" })).toBeVisible();
  expect(await readStoredToken(page)).toBeNull();
  // The form itself must still be showing — a failed login never proceeds
  // into the authenticated app.
  await expect(page.getByLabel("電子郵件")).toBeVisible();

  // Password/token hygiene: never rendered as visible text anywhere on the
  // page (e.g. echoed into an error message), and never logged. This
  // deliberately checks rendered TEXT (innerText), not page.content()'s raw
  // HTML: a live password <input>'s current value is always reflected into
  // its own `value` attribute when the browser serializes outerHTML — that
  // is normal, unavoidable behavior for every password field on every site
  // (the DOM has to hold the plaintext value somewhere for the user to see
  // it unmasked or for the form to submit it), not something this app
  // leaks, so asserting against it there would be checking for an
  // impossible condition rather than a real hygiene issue.
  const bodyText = await page.locator("body").innerText();
  expect(bodyText).not.toContain("totally-wrong-password");
  expect(consoleTexts.join("\n")).not.toContain("totally-wrong-password");
});

test("logout clears the stored token and returns to the sign-in screen", async ({ page }) => {
  await page.route("**/api/v1/auth/login", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ access_token: TEST_TOKEN, token_type: "bearer" }) });
  });
  await page.route("**/api/v1/auth/me", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TEST_USER) });
  });

  await page.goto("/test-harness/auth");
  await page.getByLabel("電子郵件").fill(TEST_USER.email);
  await page.getByLabel("密碼").fill("correct-horse-battery-staple");
  await page.getByRole("button", { name: "登入" }).click();
  await expect.poll(() => readStoredToken(page)).toBe(TEST_TOKEN);

  await page.getByRole("button", { name: "登出" }).click();

  await expect(page.getByLabel("電子郵件")).toBeVisible();
  expect(await readStoredToken(page)).toBeNull();
});

test("a stored token is validated against /auth/me and restores the session on reload", async ({ page }) => {
  let meRequests = 0;
  await page.route("**/api/v1/auth/me", async (route) => {
    meRequests += 1;
    expect(route.request().headers()["authorization"]).toBe(`Bearer ${TEST_TOKEN}`);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TEST_USER) });
  });

  await page.goto("/test-harness/auth");
  await expect(page.getByLabel("電子郵件")).toBeVisible();
  await page.evaluate((args) => window.sessionStorage.setItem(args.key, args.token), { key: TOKEN_STORAGE_KEY, token: TEST_TOKEN });

  await page.reload();

  await expect(page.getByLabel("電子郵件")).toBeHidden();
  expect(meRequests).toBeGreaterThan(0);
});

test("an invalid or expired /auth/me response clears the stored token", async ({ page }) => {
  await page.route("**/api/v1/auth/me", async (route) => {
    await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Could not validate credentials" }) });
  });

  await page.goto("/test-harness/auth");
  await page.evaluate((args) => window.sessionStorage.setItem(args.key, args.token), { key: TOKEN_STORAGE_KEY, token: "an-expired-or-garbage-token" });

  await page.reload();

  await expect(page.getByLabel("電子郵件")).toBeVisible();
  expect(await readStoredToken(page)).toBeNull();
});
