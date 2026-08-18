/** SharedArrayBuffer/pthreads require every application response to be cross-origin isolated. */
const nextConfig = {
  // React Strict Mode double-invokes effects (mount -> cleanup -> mount) —
  // deliberately, as a dev-time bug-finding aid — but this ONLY happens
  // under `next dev`; production builds strip these checks regardless of
  // this setting, so disabling it has no production behavior impact.
  // This repo's CI is forced onto `next dev` (never `next build`/`next
  // start`) by a pre-existing, unrelated broken import elsewhere in the app
  // — see .github/workflows/frontend-auth-checks.yml's comments — which
  // means, without this, the double-invoke would fire for every dev-mode
  // e2e run against any effect-driven code. This was confirmed (not
  // assumed) as a real, reproducible cause of e2e failures: with Strict
  // Mode on, features/project-status/use-project-status.ts's SSE effect
  // issued two requests to the same mocked endpoint only 3ms apart — far
  // too fast to be its own ~1s+ reconnect backoff, and its 401-handling
  // path doesn't schedule a reconnect at all — which is exactly Strict
  // Mode's double-mount signature (see frontend/e2e/project-status-transport.spec.ts).
  reactStrictMode: false,
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
      { key: "Cross-Origin-Embedder-Policy", value: "require-corp" },
      { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
    ] }];
  },
};

export default nextConfig;
