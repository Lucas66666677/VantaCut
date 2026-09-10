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
 *  * **recovery** -- a render costs a credit and can outlast the page. The
 *    ids must survive a reload, resuming must never submit a second render,
 *    and a stalled poll must offer a way forward rather than a dead end.
 */

const TOKEN_STORAGE_KEY = "vantacut_access_token";
const TEST_TOKEN = "studio-export-token";
const TEST_USER = { id: "22222222-2222-2222-2222-222222222222", email: "studio@example.com", display_name: null, is_active: true };
const PROJECT = { id: "33333333-3333-3333-3333-333333333333", name: "既有專案", description: null, lifecycle_state: "active", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" };
const ASSET_ID = "55555555-5555-5555-5555-555555555555";
/** The second upload in the A-then-B cases. */
const ASSET_ID_B = "5b5b5b5b-5b5b-5b5b-5b5b-5b5b5b5b5b5b";
const TIMELINE_ID = "66666666-6666-6666-6666-666666666666";
const RENDER_JOB_ID = "77777777-7777-7777-7777-777777777777";
const TIMELINE = { id: TIMELINE_ID, project_id: PROJECT.id, name: "第一版剪輯", version: 1, is_current: true, created_at: "2026-09-09T00:00:00Z", updated_at: "2026-09-09T00:00:00Z" };
const STORAGE_HOST = "https://storage.example.invalid";

function statusEvent(fields: Record<string, unknown>): string {
  // `updated_at` matters: the panel identifies a publish by it, so that the
  // SSE client replaying the same body on reconnect is not mistaken for a
  // fresh event about a newer asset.
  return `event: status\ndata: ${JSON.stringify({ project_id: PROJECT.id, progress: 0, stage: "idle", status: "processing", updated_at: "2026-09-09T00:00:00Z", ...fields })}\n\n`;
}

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

interface Backend {
  /** SSE body for the project status stream. */
  status: string;
  /** Asset ids handed out per upload, in order. Defaults to one. */
  assetIds?: string[];
  /** Every `source_asset_id` the client asked to build a timeline from. */
  timelineRequests?: string[];
  /**
   * When the stream delivers that body.
   *
   * Defaults to "after-upload", which is what the backend actually does:
   * `process_new_media` is enqueued by the multipart-complete handler, so
   * every `media_*` publish for an asset necessarily follows its upload.
   * "immediate" models a status snapshot that predates this upload -- a
   * restored session, or readiness left over from an earlier asset.
   */
  statusGate?: "immediate" | "after-upload";
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

  let uploadBegan = backend.statusGate === "immediate";
  const assetIds = backend.assetIds ?? [ASSET_ID];
  let uploadIndex = -1;
  const currentAsset = () => assetIds[Math.min(Math.max(uploadIndex, 0), assetIds.length - 1)];

  await page.route("**/api/v1/**", async (route: Route) => {
    const { pathname } = new URL(route.request().url());

    if (pathname === "/api/v1/auth/me") return json(route, 200, TEST_USER);
    if (pathname === "/api/v1/projects") return json(route, 200, [PROJECT]);
    if (pathname.endsWith("/status")) {
      for (let attempt = 0; attempt < 200 && !uploadBegan; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      return route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
        // Stamped per upload, because each publish carries its own
        // `updated_at` and the panel identifies events by it. Re-deliveries
        // within one upload stay identical (the SSE client replays the last
        // event, and that must not read as new); a second upload produces a
        // genuinely different event, as a second `process_new_media` would.
        body: backend.status.replace(
          '"updated_at":"2026-09-09T00:00:00Z"',
          `"updated_at":"2026-09-09T00:00:0${Math.max(uploadIndex, 0)}Z"`,
        ),
      });
    }
    if (pathname.endsWith("/timelines")) {
      const body = route.request().postDataJSON() as { source_asset_id?: string } | null;
      if (body?.source_asset_id) backend.timelineRequests?.push(body.source_asset_id);
      if (backend.timelines) return backend.timelines(route);
      return json(route, 201, TIMELINE);
    }
    if (pathname === "/api/v1/media/multipart-upload/initiate") {
      uploadBegan = true;
      uploadIndex += 1;
      return json(route, 201, { asset_id: currentAsset(), storage_key: "k", upload_id: "u", part_size_bytes: 16 * 1024 * 1024, expires_in: 900 });
    }
    if (pathname === "/api/v1/media/multipart-upload/part-url") {
      return json(route, 200, { upload_url: `${STORAGE_HOST}/part-1` });
    }
    if (pathname === "/api/v1/media/multipart-upload/complete") {
      return json(route, 200, { id: currentAsset(), status: "processing" });
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


test("a reload resumes the running job without submitting another render", async ({ page }) => {
  // The blocker review found: the panel told users to refresh while the
  // asset, timeline and render-job ids lived only in React state, so
  // refreshing destroyed a render the user had already paid a credit for.
  let renderCalls = 0;
  let downloadPolls = 0;

  await installBackend(page, {
    status: MEDIA_READY,
    render: (route) => {
      renderCalls += 1;
      return json(route, 202, { render_job_id: RENDER_JOB_ID, task_id: "t", subscription_tier: "free", render_credits_remaining: 4, watermark_applied: true });
    },
    download: (route) => {
      downloadPolls += 1;
      // Never finishes before the reload, so the reload has something to resume.
      return downloadPolls <= 2
        ? json(route, 404, { detail: "Completed render not found" })
        : json(route, 200, { download_url: `${STORAGE_HOST}/render.mp4` });
    },
  });

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await page.getByRole("button", { name: "導出影片" }).click();
  await page.getByRole("button", { name: "確認並導出" }).click();
  await expect(page.getByText(/方案 免費／剩餘點數 4/)).toBeVisible();
  expect(renderCalls).toBe(1);

  await page.reload();

  // Straight back into the queued state, with the credit outcome intact --
  // and, crucially, without a second render being submitted.
  await expect(page.getByText(/方案 免費／剩餘點數 4/)).toBeVisible();
  expect(renderCalls).toBe(1);

  await expect(page.getByRole("link", { name: "下載影片" })).toBeVisible({ timeout: 20_000 });
  expect(renderCalls).toBe(1);
});

test("a transient polling failure offers a free retry that resumes the same job", async ({ page }) => {
  let renderCalls = 0;
  let downloadPolls = 0;

  await installBackend(page, {
    status: MEDIA_READY,
    render: (route) => {
      renderCalls += 1;
      return json(route, 202, { render_job_id: RENDER_JOB_ID, task_id: "t", subscription_tier: "free", render_credits_remaining: 4, watermark_applied: true });
    },
    download: (route) => {
      downloadPolls += 1;
      // A 503 is not "no render": it is the query failing, and the panel must
      // not present that as a lost export.
      if (downloadPolls === 1) return json(route, 503, { detail: "temporarily unavailable" });
      return json(route, 200, { download_url: `${STORAGE_HOST}/render.mp4` });
    },
  });

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await page.getByRole("button", { name: "導出影片" }).click();
  await page.getByRole("button", { name: "確認並導出" }).click();

  const retry = page.getByRole("button", { name: "重新查詢導出狀態" });
  await expect(retry).toBeVisible();
  // The receipt stays on screen through the failure: the credit was spent and
  // saying otherwise would be a lie.
  await expect(page.getByText(/方案 免費／剩餘點數 4/)).toBeVisible();
  await expect(page.getByText(/這次查詢不會重新送出渲染/)).toBeVisible();

  await retry.click();

  await expect(page.getByRole("link", { name: "下載影片" })).toBeVisible({ timeout: 20_000 });
  expect(renderCalls).toBe(1);
});

test("a cold-storage restore offers a retry that goes back through the confirmation", async ({ page }) => {
  // Retrying after hydration does spend another credit, so unlike a poll
  // retry it must be confirmed again rather than fired straight off.
  let renderCalls = 0;
  await installBackend(page, {
    status: MEDIA_READY,
    render: (route) => {
      renderCalls += 1;
      return json(route, 202, { detail: { code: "cold_storage_hydration_started", hydration_job_id: "hj-1", message: "正在從冷庫調回高畫質素材，預計需要 12 小時", estimated_ready_at: null } });
    },
  });

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await page.getByRole("button", { name: "導出影片" }).click();
  await page.getByRole("button", { name: "確認並導出" }).click();
  await expect(page.getByText(/正在從冷庫調回高畫質素材/)).toBeVisible();
  expect(renderCalls).toBe(1);

  await page.getByRole("button", { name: "再試一次導出" }).click();

  // The confirmation, not another render: still one call so far.
  await expect(page.getByRole("group", { name: "確認導出" })).toBeVisible();
  expect(renderCalls).toBe(1);

  await page.getByRole("button", { name: "確認並導出" }).click();
  await expect.poll(() => renderCalls).toBe(2);
});

test("a reload before any render resumes at the timeline rather than re-uploading", async ({ page }) => {
  let timelineCalls = 0;
  await installBackend(page, {
    status: MEDIA_READY,
    timelines: (route) => { timelineCalls += 1; return json(route, 201, TIMELINE); },
  });

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await expect(page.getByText("時間軸已就緒。")).toBeVisible();
  expect(timelineCalls).toBe(1);

  await page.reload();

  await expect(page.getByRole("button", { name: "導出影片" })).toBeVisible();
  expect(timelineCalls).toBe(1);
});


test("another account on the same browser does not inherit the job", async ({ page }) => {
  // The record is keyed by user id. Without that, signing in as someone else
  // on a shared browser would show them a stranger's render job -- which the
  // backend would refuse to serve (it checks job.project.owner_id), but which
  // should never have been offered.
  let currentUser = TEST_USER;
  let renderCalls = 0;
  // Same ordering the backend enforces: readiness is published only after the
  // upload that produced the asset. See installBackend's statusGate.
  let uploadBegan = false;

  await page.addInitScript(
    ([key, token]) => window.sessionStorage.setItem(key, token),
    [TOKEN_STORAGE_KEY, TEST_TOKEN] as const,
  );
  await page.route("**/api/v1/**", async (route: Route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === "/api/v1/auth/me") return json(route, 200, currentUser);
    if (pathname === "/api/v1/projects") return json(route, 200, [PROJECT]);
    if (pathname.endsWith("/status")) {
      for (let attempt = 0; attempt < 200 && !uploadBegan; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      return route.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: MEDIA_READY });
    }
    if (pathname.endsWith("/timelines")) return json(route, 201, TIMELINE);
    if (pathname === "/api/v1/media/multipart-upload/initiate") { uploadBegan = true; return json(route, 201, { asset_id: ASSET_ID, storage_key: "k", upload_id: "u", part_size_bytes: 16 * 1024 * 1024, expires_in: 900 }); }
    if (pathname === "/api/v1/media/multipart-upload/part-url") return json(route, 200, { upload_url: `${STORAGE_HOST}/part-1` });
    if (pathname === "/api/v1/media/multipart-upload/complete") return json(route, 200, { id: ASSET_ID, status: "processing" });
    if (pathname.endsWith("/download-url")) return json(route, 404, { detail: "Completed render not found" });
    if (pathname.endsWith("/render")) {
      renderCalls += 1;
      return json(route, 202, { render_job_id: RENDER_JOB_ID, task_id: "t", subscription_tier: "free", render_credits_remaining: 4, watermark_applied: true });
    }
    return json(route, 200, {});
  });
  const cors = { "access-control-allow-origin": "*", "access-control-allow-methods": "PUT, GET, OPTIONS", "access-control-allow-headers": "*", "access-control-expose-headers": "etag" };
  await page.route(`${STORAGE_HOST}/**`, (route: Route) =>
    route.request().method() === "OPTIONS"
      ? route.fulfill({ status: 204, headers: cors, body: "" })
      : route.fulfill({ status: 200, headers: { ...cors, etag: '"etag-1"' }, body: "" }));

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await page.getByRole("button", { name: "導出影片" }).click();
  await page.getByRole("button", { name: "確認並導出" }).click();
  await expect(page.getByText(/方案 免費／剩餘點數 4/)).toBeVisible();

  // Same browser, same tab, different account.
  currentUser = { ...TEST_USER, id: "99999999-9999-9999-9999-999999999999", email: "other@example.com" };
  await page.reload();

  await expect(page.getByText("先加入一段影片，導出選項就會出現。")).toBeVisible();
  await expect(page.getByText(/剩餘點數 4/)).toHaveCount(0);
  expect(renderCalls).toBe(1);
});


test("media_ready arriving before the upload completes still unlocks the timeline", async ({ page }) => {
  // The race review found. The readiness effect used to depend only on the
  // status object while reading the asset id from a ref: readiness published
  // before the complete response left the ref empty, the effect returned, and
  // an unchanged status never re-ran it. The panel sat on "正在處理素材"
  // forever with the asset perfectly ready.
  let uploadBegan = false;
  let readinessDelivered = false;
  // Held by the test, so the window where readiness is known and the asset id
  // is not can be observed rather than raced through.
  let releaseComplete = false;

  await page.addInitScript(
    ([key, token]) => window.sessionStorage.setItem(key, token),
    [TOKEN_STORAGE_KEY, TEST_TOKEN] as const,
  );
  const cors = { "access-control-allow-origin": "*", "access-control-allow-methods": "PUT, GET, OPTIONS", "access-control-allow-headers": "*", "access-control-expose-headers": "etag" };
  await page.route(`${STORAGE_HOST}/**`, (route: Route) =>
    route.request().method() === "OPTIONS"
      ? route.fulfill({ status: 204, headers: cors, body: "" })
      : route.fulfill({ status: 200, headers: { ...cors, etag: '"etag-1"' }, body: "" }));

  const settle = async (predicate: () => boolean) => {
    for (let attempt = 0; attempt < 200 && !predicate(); attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  };

  await page.route("**/api/v1/**", async (route: Route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === "/api/v1/auth/me") return json(route, 200, TEST_USER);
    if (pathname === "/api/v1/projects") return json(route, 200, [PROJECT]);
    if (pathname.endsWith("/status")) {
      // Readiness is withheld until the upload has actually begun, then sent.
      await settle(() => uploadBegan);
      readinessDelivered = true;
      return route.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: MEDIA_READY });
    }
    if (pathname === "/api/v1/media/multipart-upload/initiate") {
      uploadBegan = true;
      return json(route, 201, { asset_id: ASSET_ID, storage_key: "k", upload_id: "u", part_size_bytes: 16 * 1024 * 1024, expires_in: 900 });
    }
    if (pathname === "/api/v1/media/multipart-upload/part-url") return json(route, 200, { upload_url: `${STORAGE_HOST}/part-1` });
    if (pathname === "/api/v1/media/multipart-upload/complete") {
      // Held until readiness is on the wire and the test says go: this
      // ordering is the whole point.
      await settle(() => readinessDelivered && releaseComplete);
      return json(route, 200, { id: ASSET_ID, status: "processing" });
    }
    if (pathname.endsWith("/timelines")) return json(route, 201, TIMELINE);
    return json(route, 200, {});
  });

  await page.goto("/studio");
  await addAVideo(page);

  // Readiness is known, the asset id is not. Offering the button here would
  // send `source_asset_id: undefined` to the backend, so it must stay hidden.
  await expect.poll(() => readinessDelivered).toBe(true);
  await page.waitForTimeout(500);
  await expect(page.getByRole("button", { name: "建立時間軸" })).toHaveCount(0);

  releaseComplete = true;

  // The assertion that used to fail: readiness landed first, and the panel
  // has to reconcile it once the asset id appears.
  await expect(page.getByRole("button", { name: "建立時間軸" })).toBeVisible();
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await expect(page.getByText("時間軸已就緒。")).toBeVisible();
});

test("a restored asset reconciles a status snapshot that arrived first", async ({ page }) => {
  // Same shape, reached by reload rather than upload: the record is restored
  // in an effect, and the status snapshot can already be on screen by then.
  await page.addInitScript(
    ([tokenKey, token, recordKey, record]) => {
      window.sessionStorage.setItem(tokenKey, token);
      window.sessionStorage.setItem(recordKey, record);
    },
    [
      TOKEN_STORAGE_KEY,
      TEST_TOKEN,
      `vantacut_studio_export:${TEST_USER.id}:${PROJECT.id}`,
      JSON.stringify({ assetId: ASSET_ID }),
    ] as const,
  );
  await installBackend(page, { status: MEDIA_READY, statusGate: "immediate" });

  await page.goto("/studio");

  // No upload in this session at all: the asset came from the stored record.
  await expect(page.getByRole("button", { name: "建立時間軸" })).toBeVisible();
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await expect(page.getByText("時間軸已就緒。")).toBeVisible();
});

test("readiness left over from an earlier asset does not unlock a new upload", async ({ page }) => {
  // The other half of the fix. Reconciling against the tracked asset must not
  // become "any media_ready will do": the status stream carries no asset id,
  // and the SSE client replays the last event on every reconnect, so a stale
  // readiness would otherwise mark a freshly uploaded file ready the instant
  // it was chosen -- and the backend would then refuse the timeline with 409.
  await installBackend(page, { status: MEDIA_READY, statusGate: "immediate" });

  await page.goto("/studio");
  // The stale event is on screen before anything is uploaded.
  await expect(page.getByText("先加入一段影片，導出選項就會出現。")).toBeVisible();

  await addAVideo(page);

  await expect(page.getByRole("status").filter({ hasText: "素材處理完成前無法導出" })).toBeVisible();
  await expect(page.getByRole("button", { name: "建立時間軸" })).toHaveCount(0);
  // Still refused after the SSE has had time to reconnect and replay it.
  await page.waitForTimeout(2_500);
  await expect(page.getByRole("button", { name: "建立時間軸" })).toHaveCount(0);
});


test("remounting the panel with a cached status still reconciles the restored asset", async ({ page }) => {
  // The case the `tracking` dependency exists for, and a real user path: the
  // workspace drops the welcome panel when an intent is applied and restores
  // it with "精簡介面". On that remount the status store is already
  // populated, so `status` is non-undefined on the panel's very first render
  // and no further event follows. A readiness effect keyed only on `status`
  // runs once, before the restore has set anything to track, and never runs
  // again -- leaving a ready asset stuck on "正在處理素材".
  await installBackend(page, { status: MEDIA_READY });

  await page.goto("/studio");
  await addAVideo(page);
  await expect(page.getByRole("button", { name: "建立時間軸" })).toBeVisible();

  // Leave the welcome workspace: the export panel unmounts with it.
  await page.getByLabel("描述剪輯需求").fill("精細調色");
  await page.getByRole("button", { name: "套用工作區" }).click();
  await expect(page.getByRole("heading", { name: "導出影片" })).toHaveCount(0);

  // Come back. The panel remounts against a store that already holds the
  // readiness event, and must restore the asset and reconcile it.
  await page.getByRole("button", { name: "精簡介面" }).click();
  await expect(page.getByRole("heading", { name: "導出影片" })).toBeVisible();
  await expect(page.getByRole("button", { name: "建立時間軸" })).toBeVisible();
});


test("leaving the welcome workspace and reloading still resumes the paid render", async ({ page }) => {
  // The stored record is written by several effects, and a remount re-runs
  // the one that only knows the asset id. If that write replaced the record
  // instead of merging into it, the renderJobId would be dropped -- and the
  // reload that recovery exists for would find nothing to resume, for a
  // render the user had already been charged for.
  let renderCalls = 0;
  let downloadPolls = 0;
  await installBackend(page, {
    status: MEDIA_READY,
    render: (route) => { renderCalls += 1; return json(route, 202, { render_job_id: RENDER_JOB_ID, task_id: "t", subscription_tier: "free", render_credits_remaining: 4, watermark_applied: true }); },
    download: (route) => {
      downloadPolls += 1;
      return downloadPolls <= 3
        ? json(route, 404, { detail: "Completed render not found" })
        : json(route, 200, { download_url: `${STORAGE_HOST}/render.mp4` });
    },
  });

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await page.getByRole("button", { name: "導出影片" }).click();
  await page.getByRole("button", { name: "確認並導出" }).click();
  await expect(page.getByText(/方案 免費／剩餘點數 4/)).toBeVisible();

  // Away and back: the panel unmounts and remounts inside the same session.
  await page.getByLabel("描述剪輯需求").fill("精細調色");
  await page.getByRole("button", { name: "套用工作區" }).click();
  await expect(page.getByRole("heading", { name: "導出影片" })).toHaveCount(0);
  await page.getByRole("button", { name: "精簡介面" }).click();
  await expect(page.getByRole("heading", { name: "導出影片" })).toBeVisible();

  // Now reload, which is what actually reads the stored record back.
  await page.reload();

  await expect(page.getByText(/方案 免費／剩餘點數 4/)).toBeVisible();
  await expect(page.getByRole("link", { name: "下載影片" })).toBeVisible({ timeout: 20_000 });
  expect(renderCalls).toBe(1);
});


test("uploading a second asset moves the export controls off the first", async ({ page }) => {
  // Review found the switch half-done: upload-start reset `tracking` and
  // cleared the stored session but never reset the phase, and the
  // tracking-to-phase effect deliberately refuses to leave the timeline,
  // queued and downloadable phases. So after building a timeline for A,
  // choosing B left the export button still pointing at A -- while A's
  // recovery record had already been erased underneath it.
  const timelineRequests: string[] = [];
  let renderCalls = 0;

  await installBackend(page, {
    status: MEDIA_READY,
    assetIds: [ASSET_ID, ASSET_ID_B],
    timelineRequests,
    render: (route) => { renderCalls += 1; return json(route, 202, { render_job_id: RENDER_JOB_ID, task_id: "t", subscription_tier: "free", render_credits_remaining: 4, watermark_applied: true }); },
  });

  await page.goto("/studio");

  // A: uploaded and built.
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await expect(page.getByText("時間軸已就緒。")).toBeVisible();
  expect(timelineRequests).toEqual([ASSET_ID]);

  // B: a second, different file.
  await addAVideo(page);

  // The export controls must leave A immediately, not stay usable.
  await expect(page.getByText("時間軸已就緒。")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "導出影片" })).toHaveCount(0);

  // B then becomes ready on its own event and builds its own timeline.
  await expect(page.getByRole("button", { name: "建立時間軸" })).toBeVisible();
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await expect(page.getByText("時間軸已就緒。")).toBeVisible();

  expect(timelineRequests).toEqual([ASSET_ID, ASSET_ID_B]);
  // Switching assets must never start a render by itself.
  expect(renderCalls).toBe(0);
});

test("a timeline response for the previous asset does not recapture the panel", async ({ page }) => {
  // The same defect one round trip later: A's create is still in flight when
  // B is chosen, and its answer must not re-point the panel at A.
  const timelineRequests: string[] = [];
  let releaseFirstTimeline = false;
  let timelineCalls = 0;

  await installBackend(page, {
    status: MEDIA_READY,
    assetIds: [ASSET_ID, ASSET_ID_B],
    timelineRequests,
    timelines: async (route) => {
      timelineCalls += 1;
      if (timelineCalls === 1) {
        for (let attempt = 0; attempt < 300 && !releaseFirstTimeline; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 50));
        }
        return json(route, 201, { ...TIMELINE, id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" });
      }
      return json(route, 201, TIMELINE);
    },
  });

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await expect(page.getByRole("status").filter({ hasText: "正在建立時間軸" })).toBeVisible();

  // Switch to B while A's create is still open.
  await addAVideo(page);
  await expect(page.getByRole("button", { name: "建立時間軸" })).toBeVisible();

  // A's answer arrives late.
  releaseFirstTimeline = true;
  await page.waitForTimeout(1_000);

  // It must be ignored: the panel is about B now, still waiting to be built.
  await expect(page.getByText("時間軸已就緒。")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "建立時間軸" })).toBeVisible();

  await page.getByRole("button", { name: "建立時間軸" }).click();
  await expect(page.getByText("時間軸已就緒。")).toBeVisible();
  expect(timelineRequests).toEqual([ASSET_ID, ASSET_ID_B]);
});

test("superseding a paid render says so rather than dropping it silently", async ({ page }) => {
  // Switching assets erases the stored record, which is where a queued
  // render's id lives. That is defensible -- one project, one slot -- but it
  // must not be invisible: the credit is already spent.
  await installBackend(page, {
    status: MEDIA_READY,
    assetIds: [ASSET_ID, ASSET_ID_B],
    render: (route) => json(route, 202, { render_job_id: RENDER_JOB_ID, task_id: "t", subscription_tier: "free", render_credits_remaining: 4, watermark_applied: true }),
  });

  await page.goto("/studio");
  await addAVideo(page);
  await page.getByRole("button", { name: "建立時間軸" }).click();
  await page.getByRole("button", { name: "導出影片" }).click();
  await page.getByRole("button", { name: "確認並導出" }).click();
  await expect(page.getByText(/方案 免費／剩餘點數 4/)).toBeVisible();

  await addAVideo(page);

  await expect(page.getByText(/先前那次導出已被這個新素材取代/)).toBeVisible();
  await expect(page.getByText(/剩餘點數 4/)).toHaveCount(0);
});
