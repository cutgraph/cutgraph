// A video's first open. /media/source/<slug> plays a source through its scrub proxy; while the
// server is still encoding that proxy it redirects to the original file instead, so the picture shows
// at once. This watches the encode and, when the proxy lands, changes the slug's URL (resolveMedia)
// so every player and still moves onto the proxy without a page reload.
import { useEffect, useSyncExternalStore } from "react";
import type { Project } from "../types/project";
import { landProxy, sourceSlug } from "./resolveMedia";

const POLL_MS = 1500;
const watched = new Set<string>();
const listeners = new Set<() => void>();
let landed = 0;

async function proxyState(slug: string): Promise<string | undefined> {
  try {
    const res = await fetch(`/api/proxy/${encodeURIComponent(slug)}`);
    return res.ok ? ((await res.json()) as { state?: string }).state : undefined;
  } catch {
    return undefined;
  }
}

async function watch(slug: string): Promise<void> {
  for (let polls = 0; ; polls += 1) {
    const state = await proxyState(slug);
    if (state !== "building") {
      // A proxy that was ready at the first ask is what the media route served from the start.
      if (state === "ready" && polls > 0) {
        landProxy(slug);
        landed += 1;
        for (const listener of listeners) listener();
      }
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Watch the proxies a project's source media is waiting on. The count changes when one lands, so a
 *  component re-resolves its media URLs; key any memoized URL on it. */
export function useSourceProxy(project: Project | null | undefined): number {
  const slugs = JSON.stringify([...new Set(project?.tracks.flatMap((t) => (t.clips ?? []).map((c) => sourceSlug(c.media) ?? "")))]
    .filter(Boolean));
  useEffect(() => {
    for (const slug of JSON.parse(slugs) as string[]) {
      if (!slug || watched.has(slug)) continue;
      watched.add(slug);
      void watch(slug);
    }
  }, [slugs]);
  return useSyncExternalStore(subscribe, () => landed);
}
