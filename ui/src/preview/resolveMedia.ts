// Proxy-else-source media resolution for the player preview (UI consumer). The browser preview
// prefers the short-GOP scrub proxy; final render (compile.py) resolves SOURCE only. Both
// preview paths share this resolution rule.
//   proxy  'proxies/A001_540.mp4' -> /media/proxy/A001_540.mp4   (served by filename)
//   source 'media/A001.mp4'       -> /media/source/A001          (the slug's server-built proxy; see sourceProxy.ts)
//   library 'workspaces/garden-1a2b3c/library/<assetId>.png' -> /media/workspace/garden-1a2b3c/<assetId>.png
// Neither present -> null (no invented route).

/** A workspace library asset as a project stores it: STATE_HOME-relative, content-addressed name. */
export const WORKSPACE_MEDIA = /^workspaces\/([a-z0-9][a-z0-9-]{0,63})\/library\/([a-f0-9]{16}\.[a-z0-9]{2,5})$/;

export function workspaceMediaRef(workspaceId: string, file: string): string {
  return `workspaces/${workspaceId}/library/${file}`;
}

function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1];
}

function stem(path: string): string {
  const base = basename(path);
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(0, dot) : base;
}

export type MediaPreference = "proxy" | "source";
export type CompareSide = "before" | "after";

/** Server-side Compare preflight for one immutable timeline clip. */
export interface MediaAvailability {
  id: string;
  side: CompareSide;
  sourceAvailable: boolean;
  proxyAvailable: boolean;
  sourceUrl?: string;
}

export function resolveMedia(
  media: { source: string; proxy?: string | null },
  preference: MediaPreference = "proxy",
  allowSourceGeneration = true,
  availability?: MediaAvailability,
): string | null {
  const sourceAvailable = availability?.sourceAvailable ?? true;
  const proxyAvailable = availability?.proxyAvailable ?? true;
  if (preference === "proxy" && media.proxy && proxyAvailable) return `/media/proxy/${encodeURIComponent(basename(media.proxy))}`;
  if (!sourceAvailable && (preference === "source" || !media.proxy || !proxyAvailable)) return null;
  const projectAsset = media.source.match(/^data\/project-media\/([A-Za-z0-9_-]+)\/([^/]+)$/);
  if (projectAsset) return `/media/project-asset/${encodeURIComponent(projectAsset[1])}/${encodeURIComponent(projectAsset[2])}`;
  const workspaceAsset = media.source.match(WORKSPACE_MEDIA);
  if (workspaceAsset) return `/media/workspace/${encodeURIComponent(workspaceAsset[1])}/${encodeURIComponent(workspaceAsset[2])}`;
  if (media.source) {
    if (allowSourceGeneration) {
      const slug = stem(media.source);
      return `/media/source/${encodeURIComponent(slug)}${landedProxies.has(slug) ? "?proxy" : ""}`;
    }
    // A Compare manifest is authoritative: if the server could not advertise an
    // exact, preflighted URL, keep the placeholder instead of inventing a raw path.
    if (availability) return availability.sourceUrl ?? null;
    const rawRef = media.source.split(/[\\/]/).map((part) => encodeURIComponent(part)).join("/");
    return `/media/source-raw/${rawRef}`;
  }
  return null;
}

// Slugs whose proxy the server finished while this page was open (sourceProxy.ts). Until then
// /media/source/<slug> redirects to the original file, and a <video> keeps reading that; the new URL
// moves it onto the proxy.
const landedProxies = new Set<string>();

export function landProxy(slug: string): void {
  landedProxies.add(slug);
}

/** The slug a clip plays through /media/source/<slug>, or null when it plays anything else. */
export function sourceSlug(media: { source: string; proxy?: string | null }): string | null {
  return resolveMedia(media)?.startsWith("/media/source/") ? stem(media.source) : null;
}
