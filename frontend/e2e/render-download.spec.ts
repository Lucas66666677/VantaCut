import { expect, test } from "@playwright/test";
import { mkdir, stat } from "node:fs/promises";
import { dirname } from "node:path";

test("editor page is reachable and the completed render MP4 is downloadable", async ({ page }) => {
  const renderUrl = process.env.RENDER_DOWNLOAD_URL;
  const destination = process.env.RENDER_DOWNLOAD_PATH;
  expect(renderUrl, "RENDER_DOWNLOAD_URL must point to the MinIO render artifact").toBeTruthy();
  expect(destination, "RENDER_DOWNLOAD_PATH must be supplied by CI").toBeTruthy();

  await page.goto("/");
  await expect(page.getByText("AI 粗剪審閱時間軸")).toBeVisible();
  // The URL is a presigned URL created after the real Celery render. Injecting an ordinary anchor
  // keeps the assertion browser-level while avoiding a test-only production export endpoint.
  await page.setContent(`<a id="render-download" href="${renderUrl}">下載完成影片</a>`);
  const [download] = await Promise.all([page.waitForEvent("download"), page.locator("#render-download").click()]);
  await mkdir(dirname(destination!), { recursive: true });
  await download.saveAs(destination!);
  expect((await stat(destination!)).size).toBeGreaterThan(10_000);
});
