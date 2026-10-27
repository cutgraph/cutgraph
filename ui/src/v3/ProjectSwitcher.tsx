// Which workspace the app is in, on every screen. The shell's context bar names it (a Story video
// shows its own) and switches to another; the Library's cast follows it, and the rail keeps it. The
// bar names it as a breadcrumb link, this menu hangs off the ▾ beside it, and archived workspaces stay out.
import { useEffect, useRef, useState } from "react";
import { BUILTIN_WORKSPACES, v3api, type Workspace } from "./api";
import { plural, useDismiss } from "./common";
import { navigate, type Route } from "./router";
import "./switcher.css";

export type ShellRoute = Route;

const BUILT_IN_NAMES: Record<string, string> = { brand: "Brand", unsorted: "Unsorted" };
const CHANGED = "v3:workspaces-changed";

/** Where switching to workspace `ws` (or to all of them) goes from a screen: the Library stays the Library. */
export function switchRoute(route: ShellRoute, ws: string | undefined): ShellRoute {
  if (route.view === "library") return ws ? { view: "library", ws } : { view: "library" };
  return ws ? { view: "project", ws } : { view: "home" };
}

/** A workspace's name for the context bar: the workspace's, else a built-in's, else nothing yet. */
export function projectName(ws: string | undefined, projects: readonly Workspace[]): string {
  if (!ws) return "All workspaces";
  return projects.find((p) => p.id === ws)?.name ?? BUILT_IN_NAMES[ws] ?? "…";
}

/** Tell every workspaces list on screen that a workspace was renamed, archived or deleted. */
export function workspacesChanged(): void {
  window.dispatchEvent(new Event(CHANGED));
}

/** The workspaces while `active` (null until they load), fetched again when `key` changes or a workspace does. */
export function useWorkspaces(active: boolean, key = ""): Workspace[] | null {
  const [projects, setProjects] = useState<Workspace[] | null>(null);
  const [changes, setChanges] = useState(0);
  useEffect(() => {
    const bump = () => setChanges((n) => n + 1);
    window.addEventListener(CHANGED, bump);
    return () => window.removeEventListener(CHANGED, bump);
  }, []);
  useEffect(() => {
    if (!active) return;
    let live = true;
    v3api.workspaces().then(
      (all) => live && setProjects(Array.isArray(all) ? all : []),
      () => live && setProjects((p) => p ?? []),
    );
    return () => {
      live = false;
    };
  }, [active, key, changes]);
  return projects;
}

/** The switch menu, opened from a labelled link (the Library's cast) or, in the context bar, a ▾. */
export default function ProjectSwitcher({ route, label }: { route: ShellRoute; label?: string }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const projects = useWorkspaces(open || label !== undefined, `${route.ws ?? ""} ${open}`);
  useDismiss(open, setOpen, wrap);

  const clients = (projects ?? []).filter((p) => !BUILTIN_WORKSPACES.has(p.id) && (!p.archived || p.id === route.ws));
  const go = (ws: string | undefined): void => {
    setOpen(false);
    navigate(switchRoute(route, ws));
  };

  return (
    <div className="menu-wrap switcher" ref={wrap}>
      {label !== undefined ? (
        <button type="button" className="switcher-btn link" aria-haspopup="menu" aria-expanded={open}
          aria-label={`${label}: ${projectName(route.ws, projects ?? [])}`} onClick={() => setOpen((o) => !o)}>
          <span>{label}</span>
          <i aria-hidden="true">▾</i>
        </button>
      ) : (
        <button type="button" className="switcher-caret" aria-haspopup="menu" aria-expanded={open}
          aria-label="Switch workspace" title="Switch workspace" onClick={() => setOpen((o) => !o)}>
          <svg viewBox="0 0 12 12" aria-hidden="true">
            <path d="M3 4.5l3 3 3-3" />
          </svg>
        </button>
      )}
      {open && (
        <div className="menu switcher-menu" role="menu" aria-label="Workspaces">
          {projects === null ? (
            <span className="menu-empty">Loading…</span>
          ) : (
            <>
              <button type="button" role="menuitemradio" aria-checked={!route.ws} onClick={() => go(undefined)}>
                <b>All workspaces</b>
                <small>{route.view === "library" ? "The brand cast only" : "Home"}</small>
              </button>
              {clients.map((p) => (
                <button key={p.id} type="button" role="menuitemradio" aria-checked={p.id === route.ws} onClick={() => go(p.id)}>
                  <b>{p.name}</b>
                  <small>{p.archived ? `Archived · ${plural(p.videoCount ?? 0, "video")}` : plural(p.videoCount ?? 0, "video")}</small>
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
