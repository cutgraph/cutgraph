import { useEffect, useRef, type ReactNode } from "react";
import { linkProps, useRoute, type Route, type View } from "./router";
import Home from "./Home";
import Project from "./Project";
import Story from "./Story";
import Library from "./Library";
import Settings from "./Settings";
import ToastHost from "./Toast";
import NewVideoFlow from "./newvideo/NewVideoFlow";
import NewScriptSheet from "./newscript/NewScriptSheet";
import ProjectSwitcher, { projectName, useWorkspaces } from "./ProjectSwitcher";
import { useProjectStore } from "../store/useProjectStore";
import "./styles.css";

type Space = Exclude<View, "project">;

const ICONS: Record<Space, ReactNode> = {
  home: <path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z" />,
  story: <path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5" />,
  library: <path d="M4 5h6v14H4zM14 5l6 2-3 12-6-2z" />,
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" />
    </>
  ),
};

const LABELS: Record<Space, string> = { home: "Home", story: "Story", library: "Library", settings: "Settings" };

function RailLink({ space, route }: { space: Space; route: Route }) {
  const active = (route.view === "project" ? "home" : route.view) === space;
  const to: Route = space === "home" ? { view: "home" } : { ...route, view: space };
  return (
    <a
      className={active ? "on" : undefined}
      aria-label={LABELS[space]}
      title={LABELS[space]}
      aria-current={active ? "page" : undefined}
      {...linkProps(to)}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        {ICONS[space]}
      </svg>
    </a>
  );
}

function Crumb({ to, children }: { to?: Route; children: ReactNode }) {
  return to ? (
    <a className="crumb" {...linkProps(to)}>
      {children}
    </a>
  ) : (
    <span className="crumb" aria-current="page">
      {children}
    </span>
  );
}

const SEP = (
  <span className="crumb-sep" aria-hidden="true">
    ›
  </span>
);

// Where the app is, on every screen: Home › the workspace › the video (or Library, Settings).
// The workspace crumb opens its Workspace page; the ▾ after it, or after Home outside a workspace, switches.
function ContextBar({ route }: { route: Route }) {
  const title = useProjectStore((s) => (route.video && s.project?.name === route.video ? s.project.title || s.project.name : null));
  const ws = route.view === "home" || route.view === "settings" ? undefined : route.ws;
  const projects = useWorkspaces(ws !== undefined, ws);
  const here =
    route.view === "story" ? title ?? (route.video ? null : "Story")
    : route.view === "library" ? "Library"
    : route.view === "settings" ? "Settings"
    : null;
  return (
    <header className="context-bar" aria-label="Workspace context">
      <nav className="crumbs" aria-label="Breadcrumb">
        <ol>
          <li>
            <Crumb to={route.view === "home" ? undefined : { view: "home" }}>Home</Crumb>
            {!ws && <ProjectSwitcher route={route} />}
          </li>
          {ws && (
            <li>
              {SEP}
              <Crumb to={route.view === "project" ? undefined : { view: "project", ws }}>{projectName(ws, projects ?? [])}</Crumb>
              <ProjectSwitcher route={route} />
            </li>
          )}
          {here && (
            <li>
              {SEP}
              <Crumb>{here}</Crumb>
            </li>
          )}
        </ol>
      </nav>
    </header>
  );
}

function Shell({ route }: { route: Route }) {
  const space = useRef<HTMLElement>(null);
  useEffect(() => {
    if (space.current) space.current.scrollTop = 0;
    if (document.scrollingElement) document.scrollingElement.scrollTop = 0;
    if (route.view === "settings" && ["#addons", "#assistant"].includes(window.location.hash)) {
      document.getElementById(window.location.hash.slice(1))?.scrollIntoView?.();
    }
  }, [route.view, route.ws]);

  let page: ReactNode;
  if (route.view === "project" && route.ws) page = <Project key={route.ws} id={route.ws} />;
  else if (route.view === "story") page = <Story ws={route.ws} video={route.video} />;
  else if (route.view === "library") page = <Library ws={route.ws} video={route.video} />;
  else if (route.view === "settings") page = <Settings />;
  else page = <Home />;

  return (
    <div className="v3">
      <nav className="rail" aria-label="Spaces">
        <div className="logo" aria-hidden="true">
          <span />
        </div>
        <RailLink space="home" route={route} />
        <RailLink space="story" route={route} />
        <RailLink space="library" route={route} />
        <span className="rail-gap" />
        <RailLink space="settings" route={route} />
      </nav>
      <div className="main">
        <ContextBar route={route} />
        <main className="space" ref={space}>
          {page}
        </main>
      </div>
      <ToastHost />
      <NewVideoFlow />
      <NewScriptSheet />
    </div>
  );
}

export default function V3App() {
  const route = useRoute();
  return <Shell route={route} />;
}
