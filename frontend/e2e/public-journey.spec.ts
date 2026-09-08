import { expect, test } from "@playwright/test";

/**
 * The advertised public journey, driven against a real deployment.
 *
 * `render-download.spec.ts` covers the last leg — it is handed a URL for an
 * artifact a render already produced. Nothing drove the legs before it against
 * a deployed environment: sign-in, upload, render request, then download.
 *
 * ## Nothing here runs by accident
 *
 * Every phase is gated on environment variables an operator sets deliberately,
 * and skips — not fails — when they are absent, so CI and a local `npm run e2e`
 * never touch a deployment. The suite never registers an account and never
 * invents a credential: the access token is issued by the operator for an
 * account they already own, and the write phase needs a second, explicit
 * opt-in on top of that.
 *
 *   VANTACUT_PUBLIC_BASE_URL       the frontend origin to drive
 *   VANTACUT_PUBLIC_API_URL        the backend origin it talks to
 *   VANTACUT_PUBLIC_ACCESS_TOKEN   a bearer token for an existing account
 *   VANTACUT_PUBLIC_PROJECT_ID     optional: reuse a project instead of creating one
 *   VANTACUT_PUBLIC_ALLOW_UPLOAD   must be exactly "1" to write anything
 *
 * ## The project is now created by the product
 *
 * This file used to *require* `VANTACUT_PUBLIC_PROJECT_ID`, because no route in
 * `backend/app/api/v1` constructed a `Project` — the only ways to obtain one
 * were the admin-token gated Platform API or a direct database insert, which is
 * what the repository's own QA fixture did. `POST /api/v1/projects` closed that,
 * so the write phase now creates its own project through the product. That is
 * the point rather than a convenience: a journey needing an operator to hand it
 * a project was not the journey a visitor takes.
 *
 * The variable survives as an override, for an operator who would rather reuse
 * one workspace than leave a probe project behind on each run.
 */

const BASE_URL = process.env.VANTACUT_PUBLIC_BASE_URL;
const API_URL = process.env.VANTACUT_PUBLIC_API_URL;
const ACCESS_TOKEN = process.env.VANTACUT_PUBLIC_ACCESS_TOKEN;
const PROJECT_ID = process.env.VANTACUT_PUBLIC_PROJECT_ID;
const ALLOW_UPLOAD = process.env.VANTACUT_PUBLIC_ALLOW_UPLOAD === "1";

const TOKEN_STORAGE_KEY = "vantacut_access_token";
const COMMIT_SHA = /^[0-9a-f]{7,40}$/;

/** A file small enough to be a courtesy to the deployment, real enough to store. */
function syntheticUpload(): { bytes: Buffer; contentType: string; filename: string } {
  // Not a real MP4: this phase proves the storage round trip — presign, PUT,
  // complete, confirm — not that FFmpeg can decode it. A render is requested
  // only when the operator supplies a timeline that already has content.
  const bytes = Buffer.alloc(64 * 1024, 0x00);
  bytes.write("VANTACUT-PUBLIC-JOURNEY-PROBE", 0, "utf8");
  return { bytes, contentType: "application/octet-stream", filename: `public-journey-probe-${Date.now()}.bin` };
}

// This suite deliberately handles a real bearer token. Playwright traces
// include browser/network state and are retained on failure by the shared
// config, so disable them here rather than risk packaging credentials in a
// local or subsequently uploaded report.
test.use({ trace: "off" });

test.describe("public journey", () => {
  test.skip(
    !BASE_URL || !API_URL || !ACCESS_TOKEN,
    "set VANTACUT_PUBLIC_BASE_URL, VANTACUT_PUBLIC_API_URL and VANTACUT_PUBLIC_ACCESS_TOKEN to drive a deployment",
  );

  test("the deployment names the build it is serving", async ({ request }) => {
    // Read-only, and first on purpose: every assertion below is answered by
    // whichever build is actually running, so a result recorded without this
    // is a result about an unknown revision.
    const response = await request.get(`${API_URL}/version`);
    expect(response.status()).toBe(200);
    const body = await response.json() as { revision?: unknown };
    expect(
      typeof body.revision === "string" && COMMIT_SHA.test(body.revision),
      `/version did not report a commit SHA: ${JSON.stringify(body)}`,
    ).toBe(true);
    test.info().annotations.push({ type: "revision", description: String(body.revision) });
  });

  test("an authenticated visitor reaches the studio", async ({ page }) => {
    // Read-only. Proves the token is accepted and the editor's import graph
    // loads on the deployed bundle, which is where a broken build shows up as
    // a blank gate rather than an error.
    await page.addInitScript(
      ([key, token]) => window.sessionStorage.setItem(key, token),
      [TOKEN_STORAGE_KEY, ACCESS_TOKEN!] as const,
    );
    await page.goto(`${BASE_URL}/studio`);
    await expect(page.getByLabel("描述剪輯需求")).toBeVisible({ timeout: 30_000 });
  });

  test("storage accepts a real upload and the asset leaves UPLOADING", async ({ request }) => {
    test.skip(!ALLOW_UPLOAD, "writes are opt-in: set VANTACUT_PUBLIC_ALLOW_UPLOAD=1");

    const authorised = { Authorization: `Bearer ${ACCESS_TOKEN}` };
    const file = syntheticUpload();

    // The leg that did not exist. Created here through the product rather than
    // accepted from the environment, so this phase exercises the journey a
    // visitor takes; the override only exists to avoid stray probe projects.
    let projectId = PROJECT_ID;
    if (!projectId) {
      const created = await request.post(`${API_URL}/api/v1/projects`, {
        headers: authorised,
        data: { name: "public journey probe" },
      });
      expect(created.status(), await created.text()).toBe(201);
      projectId = (await created.json() as { id: string }).id;
    }

    const initiate = await request.post(`${API_URL}/api/v1/media/multipart-upload/initiate`, {
      headers: authorised,
      data: {
        project_id: projectId,
        filename: file.filename,
        size_bytes: file.bytes.byteLength,
        content_type: file.contentType,
        media_type: "video",
      },
    });
    expect(initiate.status(), await initiate.text()).toBe(201);
    const started = await initiate.json() as { asset_id: string; upload_id: string };

    const partUrl = await request.post(`${API_URL}/api/v1/media/multipart-upload/part-url`, {
      headers: authorised,
      data: { asset_id: started.asset_id, upload_id: started.upload_id, part_number: 1 },
    });
    expect(partUrl.status(), await partUrl.text()).toBe(200);
    const { upload_url } = await partUrl.json() as { upload_url: string };

    // The presigned URL is signed against the *public* endpoint, so this PUT
    // is the only check that the browser-facing host is reachable and its CORS
    // policy exposes ETag. `/ready/storage` reports a server-side HeadBucket,
    // which cannot establish either.
    const put = await request.fetch(upload_url, { method: "PUT", data: file.bytes });
    expect(put.status(), `presigned PUT failed against ${new URL(upload_url).origin}`).toBe(200);
    const etag = put.headers()["etag"];
    expect(etag, "storage did not return an ETag; multipart cannot be completed").toBeTruthy();

    const complete = await request.post(`${API_URL}/api/v1/media/multipart-upload/complete`, {
      headers: authorised,
      data: { asset_id: started.asset_id, upload_id: started.upload_id, parts: [{ part_number: 1, etag }] },
    });
    expect(complete.status(), await complete.text()).toBe(200);
    const asset = await complete.json() as { status: string };
    expect(asset.status, "the asset never left UPLOADING").not.toBe("uploading");
  });

  test("a completed render is downloadable by its owner", async ({ request }) => {
    const renderJobId = process.env.VANTACUT_PUBLIC_RENDER_JOB_ID;
    test.skip(
      !renderJobId,
      "set VANTACUT_PUBLIC_RENDER_JOB_ID to a completed render owned by this account",
    );

    const response = await request.get(
      `${API_URL}/api/v1/timelines/render-jobs/${renderJobId}/download-url`,
      { headers: { Authorization: `Bearer ${ACCESS_TOKEN}` } },
    );
    expect(response.status(), await response.text()).toBe(200);
    const { download_url } = await response.json() as { download_url: string };

    // Range request: proves the artifact is fetchable from the browser-facing
    // host without pulling the whole render across the network.
    const artifact = await request.fetch(download_url, { method: "GET", headers: { Range: "bytes=0-1023" } });
    expect([200, 206]).toContain(artifact.status());
    expect((await artifact.body()).byteLength).toBeGreaterThan(0);
  });
});
