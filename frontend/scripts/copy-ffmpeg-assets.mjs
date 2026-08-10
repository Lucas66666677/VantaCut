import { access, cp, mkdir } from "node:fs/promises";
import { join } from "node:path";

// The core is hosted by our own Next.js origin. This avoids cross-origin Worker/CORS issues
// and lets a deployment pin the WASM binary via package-lock.json.
const source = join(process.cwd(), "node_modules", "@ffmpeg", "core", "dist", "umd");
const destination = join(process.cwd(), "public", "ffmpeg-core");
await mkdir(destination, { recursive: true });
await cp(source, destination, { recursive: true, force: true });

// `@ffmpeg/core-mt` is optional in local development. When installed, deployment
// automatically gets pthread-enabled FFmpeg under a separate, cacheable path.
const threadedSource = join(process.cwd(), "node_modules", "@ffmpeg", "core-mt", "dist", "umd");
try {
  await access(threadedSource);
  await cp(threadedSource, join(process.cwd(), "public", "ffmpeg-core-mt"), { recursive: true, force: true });
} catch { /* Single-thread core remains a safe fallback. */ }
