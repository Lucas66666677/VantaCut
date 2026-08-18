import { expect, test, type Page } from "@playwright/test";

/**
 * Focused security-regression coverage for the frontend auth foundation
 * (lib/auth/*, features/auth/*). Network boundaries are mocked via
 * page.route() — POST /auth/login, POST /auth/register, GET /auth/me — the
 * auth store/UI logic itself runs for real. See
 * e2e/project-status-transport.spec.ts for the SSE/WebSocket transport
 * coverage.
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

  await page.goto("/studio");
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

  await page.goto("/studio");
  await page.getByLabel("電子郵件").fill("nope@example.com");
  await page.getByLabel("密碼").fill("totally-wrong-password");
  await page.getByRole("button", { name: "登入" }).click();

  await expect(page.getByRole("alert")).toBeVisible();
  expect(await readStoredToken(page)).toBeNull();
  // The form itself must still be showing — a failed login never proceeds
  // into the authenticated app.
  await expect(page.getByLabel("電子郵件")).toBeVisible();

  // Password/token hygiene: never rendered into the page, never logged.
  const content = await page.content();
  expect(content).not.toContain("totally-wrong-password");
  expect(consoleTexts.join("\n")).not.toContain("totally-wrong-password");
});

test("logout clears the stored token and returns to the sign-in screen", async ({ page }) => {
  await page.route("**/api/v1/auth/login", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ access_token: TEST_TOKEN, token_type: "bearer" }) });
  });
  await page.route("**/api/v1/auth/me", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TEST_USER) });
  });

  await page.goto("/studio");
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

  await page.goto("/studio");
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

  await page.goto("/studio");
  await page.evaluate((args) => window.sessionStorage.setItem(args.key, args.token), { key: TOKEN_STORAGE_KEY, token: "an-expired-or-garbage-token" });

  await page.reload();

  await expect(page.getByLabel("電子郵件")).toBeVisible();
  expect(await readStoredToken(page)).toBeNull();
});
