import { expect, test } from "@playwright/test";
import { mkdir, stat } from "node:fs/promises";
import { dirname } from "node:path";

test("editor page is reachable and the completed render MP4 is downloadable", async ({ page }) => {
  const renderUrl = process.env.RENDER_DOWNLOAD_URL;
  const destination = process.env.RENDER_DOWNLOAD_PATH;
  const accessToken = process.env.RENDER_DOWNLOAD_ACCESS_TOKEN;
  expect(renderUrl, "RENDER_DOWNLOAD_URL must point to the MinIO render artifact").toBeTruthy();
  expect(destination, "RENDER_DOWNLOAD_PATH must be supplied by CI").toBeTruthy();
  expect(accessToken, "RENDER_DOWNLOAD_ACCESS_TOKEN must be the backend-issued JWT for the seeded fixture user").toBeTruthy();

  await page.addInitScript((token) => {
    window.sessionStorage.setItem("vantacut_access_token", token);
  }, accessToken as string);

  await page.goto("/studio");
  const intentInput = page.getByPlaceholder("例如：幫我精細調色，或調整人聲混音");
  await expect(intentInput).toBeVisible();
  await intentInput.fill("剪輯");
  await page.getByRole("button", { name: "交給 AI" }).click();
  await expect(page.getByText("AI 粗剪審閱時間軸")).toBeVisible();
  // The URL is a presigned URL created after the real Celery render. Request
  // that real artifact in a browser page rather than replacing the studio UI
  // with a test-only download element.
  const downloadPage = await page.context().newPage();
  const [download] = await Promise.all([downloadPage.waitForEvent("download"), downloadPage.goto(renderUrl!)]);
  await mkdir(dirname(destination!), { recursive: true });
  await download.saveAs(destination!);
  await downloadPage.close();
  expect((await stat(destination!)).size).toBeGreaterThan(10_000);
});
