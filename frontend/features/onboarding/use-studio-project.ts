"use client";

import { useEffect, useState } from "react";

import { authenticatedFetch } from "@/lib/api/authenticated-fetch";
import { useAuthStore } from "@/lib/auth/auth-store";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * The project every cloud leg of the studio is scoped to.
 *
 * `LocalMediaBin` uploads only when it is given one, and the studio never was,
 * so every file stayed in the browser while the workspace promised background
 * cloud sync. This resolves one: reuse the visitor's most recent project, or
 * create their first.
 *
 * **List before creating.** That ordering is the whole reason a refresh does
 * not mint a workspace each time: someone reopening the studio, or landing on
 * it from a second device, sees the project they already have. It is also why
 * the create is reached only on a genuinely empty listing.
 *
 * The launchpad is mounted more than once per load -- `StudioPage` wraps it in
 * `Suspense` and `useSearchParams` suspends the first render -- so the listing
 * is observed to run twice. That is an idempotent GET and is left alone: an
 * in-flight guard was tried and measured, and made no difference to either the
 * listing or the create, so it was removed rather than kept as decoration.
 * `e2e/studio-project-provisioning.spec.ts` asserts what actually matters, that
 * a second project is never created.
 *
 * The studio holds back the media picker while this is loading, because a file
 * selected before `projectId` arrives is kept local and is not retried later.
 * Failure is still not fatal: `projectId` stays `undefined`, the editor opens
 * in local-only mode, and an explicit warning replaces the cloud-sync promise.
 */
export type StudioProject = {
  projectId?: string;
  status: "idle" | "loading" | "ready" | "error";
};

type ProjectSummary = { id?: string };

async function resolveProject(): Promise<string | undefined> {
  const listed = await authenticatedFetch(`${API_URL}/api/v1/projects?limit=1`);
  if (!listed.ok) throw new Error(`listing projects failed (${listed.status})`);
  const existing = (await listed.json()) as ProjectSummary[];
  const found = Array.isArray(existing) ? existing[0]?.id : undefined;
  if (found) {
    return found;
  }

  const created = await authenticatedFetch(`${API_URL}/api/v1/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!created.ok) throw new Error(`creating a project failed (${created.status})`);
  const project = (await created.json()) as ProjectSummary;
  if (!project.id) throw new Error("the created project carried no id");
  return project.id;
}

export function useStudioProject(): StudioProject {
  const status = useAuthStore((state) => state.status);
  const [state, setState] = useState<StudioProject>({ status: "idle" });

  useEffect(() => {
    if (status !== "authenticated") {
      return;
    }
    let cancelled = false;
    setState({ status: "loading" });

    resolveProject()
      .then((projectId) => {
        if (!cancelled) setState({ projectId, status: "ready" });
      })
      .catch(() => {
        // Not surfaced as an error screen: see the note above. The media bin
        // already renders the local-only state this leaves it in.
        if (!cancelled) setState({ status: "error" });
      });

    return () => {
      cancelled = true;
    };
  }, [status]);

  return state;
}
