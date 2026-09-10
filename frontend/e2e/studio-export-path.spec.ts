import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Upload -> timeline -> render -> download, driven through the studio UI.
 *
 * The workspace had no export surface at all: `workspace-registry` registers
 * timeline, inspector, colour wheels, scopes and audio mixer, none of which
 * exports, and `ClientRenderExport` is mounted nowhere. An uploaded video
 * could be edited and never leave the browser.
 *
 * Every leg here is driven by clicking the real UI. Network boundaries are
 * mocked with `page.route()` the way `studio-project-provisioning.spec.ts`
 * does; the panel's own state machine, the SSE parsing and the request
 * building all run for real. Nothing is inserted into a database, no fixture
 * is seeded behind the UI's back, and nothing reaches a deployment.
 *
 * ## One dispatcher rather than several routes
 *
 * `/api/v1/projects`, `/api/v1/projects/{id}/timelines` and
 * `/api/v1/projects/{id}/status` share a prefix, so glob patterns broad enough
 * to catch the listing (which is called as `?limit=1`) also swallow the other
 * two. The first version of this file registered them separately and every
 * test failed with the panel stuck in its no-project state, because the
 * listing was never intercepted at all. A single handler keyed on the
 * pathname cannot have that problem.
 *
 * The states worth being precise about, and why each has a test:
 *
 *  * **processing** -- the panel must not offer an export before
 *    `process_new_media` has probed the file, because the duration is unknown
 *    and the backend would refuse. The progress shown is the worker's own.
 *  * **credit confirmation** -- the app cannot read a credit balance
 *    (`GET /auth/me` returns id, email, display name, is_active and nothing
 *    else), so the confirmation must state the rule without inventing a
 *    number, and the real tier/balance must appear only after the render
 *    response carries them.
 *  * **blocked** -- out of credits is a 402 with a message worth showing.
 *  * **cold storage** -- a started restore and a queued render both answer
 *    202; only the body tells them apart.
 */

const TOKEN_STORAGE_KEY = "vantacut_access_token";
const TEST_TOKEN = "studio-export-token";
const TEST_USER = { id: "22222222-2222-2222-2222-222222222222", email: "studio@example.com", display_name: null, is_active: true };
const PROJECT = { id: "33333333-3333-3333-3333-333333333333", name: "既有專案", description: null, lifecycle_state: "active", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" };
const ASSET_ID = "55555555-5555-5555-5555-555555555555";
const TIMELINE_ID = "66666666-6666-6666-6666-666666666666";
const RENDER_JOB_ID = "77777777-7777-7777-7777-777777777777";
const TIMELINE = { id: TIMELINE_ID, project_id: PROJECT.id, name: "第一版剪輯", version: 1, is_current: true, created_at: "2026-09-09T00:00:00Z", updated_at: "2026-09-09T00:00:00Z" };
const STORAGE_HOST = "https://storage.example.invalid";

function statusEvent(fields: Record<string, unknown>): string {
  return `event: status\ndata: ${JSON.stringify({ project_id: PROJECT.id, progress: 0, stage: "idle", status: "processing", ...fields })}\n\n`;
}

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

interface Backend {
  /** SSE body for the project status stream. */
  status: string;
  /** Answers `POST /projects/{id}/timelines`. Defaults to a created timeline. */
  timelines?: (route: Route) => Promise<void>;
  /** Answers `POST /timelines/{id}/render`. */
  render?: (route: Route) => Promise<void>;
  /** Answers `GET /timelines/render-jobs/{id}/download-url`. */
  download?: (route: Route) => Promise<void>;
}

async function installBackend(page: Page, backend: Backend): Promise<void> {
  await page.addInitScript(
    ([key, token]) => window.sessionStorage.setItem(key, token),
    [TOKEN_STORAGE_KEY, TEST_TOKEN] as const,
  );
  // The presigned PUT is cross-origin, and `route.fulfill` does not exempt it
  // from CORS: the browser still preflights a PUT carrying `video/mp4`, and
  // still hides `ETag` from `response.headers.get()` unless the response
  // exposes it. Modelling that is not test scaffolding for its own sake --
  // an ETag the bucket does not expose is exactly how this leg fails in
  // production, and the first version of this mock returned a bare 200 and
  // reproduced it: "第 1 段上傳失敗".
  const cors = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "PUT, GET, OPTIONS",
    "access-control-allow-headers": "*",
    "access-control-expose-headers": "etag",
  };
  await page.route(`${STORAGE_HOST}/**`, (route: Route) =>
    route.request().method() === "OPTIONS"
      ? route.fulfill({ status: 204, headers: cors, body: "" })
      : route.fulfill({ status: 200, headers: { ...cors, etag: '"etag-1"' }, body: "" }));

  await page.route("**/api/v1/**", async (route: Route) => {
    const { pathname } = new URL(route.request().url());

    if (pathname === "/api/v1/auth/me") return json(route, 200, TEST_USER);
    if (pathname === "/api/v1/projects") return json(route, 200, [PROJECT]);
    if (pathname.endsWith("/status")) {
      return route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
        body: backend.status,
      });
    }
    if (pathname.endsWith("/timelines")) {
      if (backend.timelines) return backend.timelines(route);
      return json(route, 201, TIMELINE);
    }
    if (pathname === "/api/v1/media/multipart-upload/initiate") {
      return json(route, 201, { asset_id: ASSET_ID, storage_key: "k", upload_id: "u", part_size_bytes: 16 * 1024 * 1024, expires_in: 900 });
    }
    if (pathname === "/api/v1/media/multipart-upload/part-url") {
      return json(route, 200, { upload_url: `${STORAGE_HOST}/part-1` });
    }
    if (pathname === "/api/v1/media/multipart-upload/complete") {
      return json(route, 200, { id: ASSET_ID, status: "processing" });
    }
    if (pathname.endsWith("/download-url")) {
      if (backend.download) return backend.download(route);
      return json(route, 404, { detail: "Completed render not found" });
    }
    if (pathname.endsWith("/render")) {
      if (backend.render) return backend.render(route);
      return json(route, 202, { render_job_id: RENDER_JOB_ID, task_id: "t", subscription_tier: "free", render_credits_remaining: 9, watermark_applied: true });
    }
    // Anything unmocked answers empty rather than reaching the network, so a
    // stray call cannot quietly become a real request.
    return json(route, 200, {});
  });
}

/** Scoped to the media bin: other panels also render file inputs. */
async function addAVideo(page: Page): Promise<void> {
  await page
    .locator('section[aria-labelledby="local-media-title"] input[type="file"]')
    .setInputFiles({ name: "clip.mp4", mimeType: "video/mp4", buffer: Buffer.from("mocked upload") });
}

const MEDIA_READY = statusEvent({ stage: "media_ready", progress: 100, status: "completed", message: "媒體預處理完成" });

test("the panel refuses to offer an export while the asset is still processing", async ({ page }) => {
  await installBackend(page, { status: statusEvent({ stage: "media_probing", progress: 20, message: "正在讀取影片格式" }) });

  await page.goto("/studio");
  await expect(page.getByRole("heading", { name: "導出影片" })).toBeVisible();
  await expect(page.getByText("先加入一段影片，導出選項就會出現。")).toBeVisible();

  await addAVideo(page);

  // The worker's own stage and percentage, not a spinner.
  await expect(page.getByText(/正在讀取影片格式（20%）/)).toBeVisible();
  await expect(page.getByRole("button", { name: "建立時間軸" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "導出影片" })).toHaveCount(0);
});

test("a failed probe is reported rather than spun on", async ({ page }) => {
  await installBackend(page, { status: statusEvent({ stage: "media_failed", status: "failed", message: "無法解析此檔案" }) });

  await page.goto("/studio");
  await addAVideo(page);

  await expect(page.getByRole("alert").filter({ hasText: "素材處理失敗：無法解析此檔案" })).toBeVisible();
  await expect(page.getByRole("button", { name: "建立時間軸" })).toHaveCount(0);
});

test("a ready asset reaches a downloadable render through the UI", async ({ page }) => {
  const renderBodies: unknown[] = [];
  let downloadPolls = 0;

  await installBackend(page, {
    status: statusEvent({ stage: "media_probing", progress: 20, message: "正在讀取影片格式" }) + MEDIA_READY,
    render: (route) => {
      renderBodies.push(route.request().postDataJSON());
      return json(route, 202, { render_job_id: RENDER_JOB_ID, task_id: "t", subscription_tier: "free", render_credits_remaining: 9, watermark_applied: true });
    },
    download: (route) => {
      downloadPolls += 1;
      return downloadPolls === 1
        ? json(route, 404, { detail: "Completed render not found" })
        : json(route, 200, { download_url: `${STORAGE_HOST}/render.mp4` });
    },
  });

  await page.goto("/studio");
  await addAVideo(page);

  await expect(page.getByText("素材已處理完成，可以建立時間軸。")).toBeVisible();
  await page.getByRole("button", { name: "建立時間軸" }).click();

  await expect(page.getByText("時間軸已就緒。")).toBeVisible();
  await page.getByRole("button", { name: "導出影片" }).click();

  // The confirmation must not invent a balance: nothing the app has read can
  // tell it one. It states the rule and defers the numbers.
  const confirmation = page.getByRole("group", { name: "確認導出" });
  await expect(confirmation).toContainText("免費方案每次導出會扣除 1 點渲染點數");
  await expect(confirmation).toContainText("實際方案與剩餘點數會在送出後顯示");
  await expect(confirmation).not.toContainText("剩餘點數 9");

  // Nothing is spent until the confirmation is accepted.
  expect(renderBodies).toHaveLength(0);
  await confirmation.getByRole("button", { name: "確認並導出" }).click();

  // Now the authoritative numbers, straight from the render response.
  await expect(page.getByText(/方案 免費／剩餘點數 9／輸出含浮水印/)).toBeVisible();
  expect(renderBodies).toEqual([{ resolution: "720p", aspect_ratio: "16:9" }]);

  // 404 means "not finished", so the panel keeps waiting and then resolves.
  await expect(page.getByRole("link", { name: "下載影片" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("link", { name: "下載影片" })).toHaveAttribute("href", `${STORAGE_HOST}/render.mp4`);
  expect(downloadPolls).toBeGreaterThanOrEqual(2);
});

test("cancelling the confirmation spends nothing", async ({ page }) => {
  let renderCalls = 0;
  await installBackend(page, {
    status: MEDIA_READY,
    render: (route) => { renderCalls += 1; return json(route, 202, {}); },
  });

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await page.getByRole("button", { name: "導出影片" }).click();
  await page.getByRole("button", { name: "取消" }).click();

  await expect(page.getByRole("button", { name: "導出影片" })).toBeVisible();
  await page.waitForTimeout(300);
  expect(renderCalls).toBe(0);
});

test("running out of credits is reported with the backend's own message", async ({ page }) => {
  await installBackend(page, {
    status: MEDIA_READY,
    render: (route) => json(route, 402, { detail: "免費渲染點數已用完，請升級 Pro 或購買點數。" }),
  });

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await page.getByRole("button", { name: "導出影片" }).click();
  await page.getByRole("button", { name: "確認並導出" }).click();

  // "方案限制" rather than "導出失敗": running out of credits is a plan
  // limit, not a malfunction, and the two rendered identically until a
  // mutation (deleting the 402 branch) passed this test unchanged.
  await expect(page.getByRole("alert").filter({ hasText: "方案限制：免費渲染點數已用完" })).toBeVisible();
  await expect(page.getByText(/導出失敗/)).toHaveCount(0);
  await expect(page.getByRole("link", { name: "下載影片" })).toHaveCount(0);
});

test("a cold-storage restore is told apart from a queued render", async ({ page }) => {
  // Both answer 202 -- the route's success status and the hydration branch's
  // HTTPException(202) -- so only the body distinguishes them. Reading the
  // status alone would show "queued" and then poll a job that does not exist.
  await installBackend(page, {
    status: MEDIA_READY,
    render: (route) => json(route, 202, { detail: { code: "cold_storage_hydration_started", hydration_job_id: "hj-1", message: "正在從冷庫調回高畫質素材，預計需要 12 小時", estimated_ready_at: null } }),
  });

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await page.selectOption('select[aria-label="導出畫質"]', "1080p");
  await page.getByRole("button", { name: "導出影片" }).click();
  await page.getByRole("button", { name: "確認並導出" }).click();

  // The follow-up sentence, not just the message: with the cold-storage
  // branch deleted the same message still reaches the screen through the
  // failure path, and asserting the message alone passed anyway.
  await expect(page.getByText(/正在從冷庫調回高畫質素材/)).toBeVisible();
  await expect(page.getByText(/素材回到熱儲存後請再導出一次/)).toBeVisible();
  await expect(page.getByText(/導出失敗/)).toHaveCount(0);
  await expect(page.getByText(/剩餘點數/)).toHaveCount(0);
  await expect(page.getByRole("link", { name: "下載影片" })).toHaveCount(0);
});
