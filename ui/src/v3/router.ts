import { useMemo, useSyncExternalStore, type MouseEvent } from "react";

// The whole v3 route lives in the query string, so the studio's single /editor/ route serves
// every screen and a reload or shared link lands on the same view.
export type View = "home" | "project" | "story" | "library" | "settings";

export type Route = { view: View; ws?: string; video?: string; section?: "addons" | "assistant" };

const VIEWS = new Set<string>(["home", "project", "story", "library", "settings"]);
const NAVIGATE = "v3:navigate";

export function parseRoute(search: string): Route {
  const q = new URLSearchParams(search);
  const legacyVideo = q.get("project");
  if (legacyVideo) return { view: "story", video: legacyVideo };
  const raw = q.get("view") ?? "home";
  const ws = q.get("ws") || undefined;
  const video = q.get("video") || undefined;
  const view = (VIEWS.has(raw) ? raw : "home") as View;
  if (view === "project" && !ws) return { view: "home" };
  return { view, ...(ws && { ws }), ...(video && { video }) };
}

export function routeSearch(route: Route): string {
  const q = new URLSearchParams();
  if (route.view !== "home") q.set("view", route.view);
  if (route.ws) q.set("ws", route.ws);
  if (route.video) q.set("video", route.video);
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function routeHref(route: Route): string {
  return window.location.pathname + routeSearch(route) + (route.section ? `#${route.section}` : "");
}

export function navigate(route: Route): void {
  window.history.pushState(null, "", routeHref(route));
  window.dispatchEvent(new Event(NAVIGATE));
}

/** Correct the current route in place (no new history entry), e.g. to name the project it is in. */
export function replaceRoute(route: Route): void {
  window.history.replaceState(null, "", routeHref(route));
  window.dispatchEvent(new Event(NAVIGATE));
}

// Plain-click navigates in place; modified clicks fall through to the browser (new tab etc.).
export function linkProps(route: Route) {
  return {
    href: routeHref(route),
    onClick: (e: MouseEvent<HTMLAnchorElement>) => {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      navigate(route);
    },
  };
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener(NAVIGATE, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(NAVIGATE, onChange);
  };
}

export function useRoute(): Route {
  const search = useSyncExternalStore(subscribe, () => window.location.search);
  return useMemo(() => parseRoute(search), [search]);
}
