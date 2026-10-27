import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { ApiError, BUILTIN_WORKSPACES, v3api, type RecentVideo, type Workspace } from "./api";
import { clock, ErrorState, errorText, plural, useDismiss, useLoad } from "./common";
import { FootageArt, IdeaArt } from "./art";
import { useNewScript } from "./newscript/store";
import { useNewVideoFlow } from "./newvideo/store";
import { workspacesChanged } from "./ProjectSwitcher";
import { linkProps, navigate } from "./router";
import ScriptCover from "./ScriptCover";
import { toast } from "./Toast";
import { AddonCard } from "./Addons";
import { addonSnapshot } from "./addonSnapshot";
import "./Welcome.css";

const ACCENTS = ["--yellow", "--cyan", "--green", "--violet", "--blue", "--coral"];
const RECENT_SHOWN = 4;

function accent(id: string): CSSProperties {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) | 0;
  return { "--c": `var(${ACCENTS[Math.abs(h) % ACCENTS.length]})` } as CSSProperties;
}

function Cover({ w }: { w: Workspace }) {
  const [broken, setBroken] = useState(false);
  if (w.coverAssetId && !broken) {
    return <img className="cover" src={v3api.assetThumbUrl(w.id, w.coverAssetId)} alt="" loading="lazy" onError={() => setBroken(true)} />;
  }
  return (
    <span className="cover cover-ph" style={accent(w.id)} aria-hidden="true">
      {w.name.trim().charAt(0).toUpperCase() || "·"}
    </span>
  );
}

// Home sorts the user's workspaces (Recently used, Name A–Z, Most videos), remembered per viewer;
// the built-ins keep their place after them.
type Sort = "recent" | "name" | "videos";

const SORTS: [Sort, string][] = [["recent", "Recently used"], ["name", "Name A–Z"], ["videos", "Most videos"]];
const SORT_KEY = "cg3-workspace-sort";

function savedSort(): Sort {
  try {
    const saved = localStorage.getItem(SORT_KEY);
    return SORTS.find(([s]) => s === saved)?.[0] ?? "recent";
  } catch {
    return "recent";
  }
}

function saveSort(sort: Sort): void {
  try {
    localStorage.setItem(SORT_KEY, sort);
  } catch {
    // private mode: the default again next visit
  }
}

const byName = (a: Workspace, b: Workspace) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });
const usedAt = (w: Workspace) => Date.parse(w.lastUsedAt ?? "") || 0;
const ORDER: Record<Sort, (a: Workspace, b: Workspace) => number> = {
  recent: (a, b) => usedAt(b) - usedAt(a) || byName(a, b),
  name: byName,
  videos: (a, b) => b.videoCount - a.videoCount || byName(a, b),
};

function sorted(all: readonly Workspace[], sort: Sort): Workspace[] {
  return [...all.filter((w) => !BUILTIN_WORKSPACES.has(w.id)).sort(ORDER[sort]), ...all.filter((w) => BUILTIN_WORKSPACES.has(w.id))];
}

function contents(w: Workspace): string {
  const parts = [w.videoCount ? plural(w.videoCount, "video") : "", w.assetCount ? plural(w.assetCount, "asset") : ""].filter(Boolean);
  return parts.join(" and ");
}

interface Actions {
  rename: (w: Workspace, name: string) => Promise<boolean>;
  archive: (w: Workspace, archived: boolean) => Promise<void>;
  remove: (w: Workspace) => Promise<boolean>;
}

type Mode = "view" | "rename" | "delete";

function WorkspaceMenu({ w, onPick }: { w: Workspace; onPick: (pick: Exclude<Mode, "view"> | "archive") => void }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  useDismiss(open, setOpen, wrap);
  const pick = (p: Exclude<Mode, "view"> | "archive") => {
    setOpen(false);
    onPick(p);
  };
  return (
    <div className="menu-wrap" ref={wrap}>
      <button type="button" className="ws-more" aria-haspopup="menu" aria-expanded={open} aria-label={`Actions for ${w.name}`}
        title="More" onClick={() => setOpen((o) => !o)}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="5" cy="12" r="1.8" />
          <circle cx="12" cy="12" r="1.8" />
          <circle cx="19" cy="12" r="1.8" />
        </svg>
      </button>
      {open && (
        <div className="menu ws-menu" role="menu" aria-label={w.name}>
          <button type="button" role="menuitem" onClick={() => pick("rename")}>Rename</button>
          {!w.archived && <button type="button" role="menuitem" onClick={() => pick("archive")}>Archive</button>}
          <button type="button" role="menuitem" className="danger" onClick={() => pick("delete")}>Delete</button>
        </div>
      )}
    </div>
  );
}

function RenameForm({ w, rename, onDone }: { w: Workspace; rename: Actions["rename"]; onDone: () => void }) {
  const [name, setName] = useState(w.name);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const next = name.trim();
    if (!next || next === w.name) return onDone();
    setBusy(true);
    if (await rename(w, next)) onDone();
    else setBusy(false);
  };
  return (
    <form className="card-text card-edit" onSubmit={(e) => void submit(e)}>
      <input autoFocus value={name} maxLength={120} aria-label={`Rename ${w.name}`} onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onDone()} />
      <div>
        <button type="button" className="ghost" disabled={busy} onClick={onDone}>Cancel</button>
        <button type="submit" className="primary" disabled={busy || !name.trim()}>Save</button>
      </div>
    </form>
  );
}

// Only an empty workspace can go. One with videos or assets says so here, before asking; the studio
// refuses the rest (characters, or a video filed meanwhile) with a reason the toast shows.
function DeleteConfirm({ w, act, onKeep }: { w: Workspace; act: Actions; onKeep: () => void }) {
  const [busy, setBusy] = useState(false);
  const inside = contents(w);
  const remove = async () => {
    setBusy(true);
    if (!(await act.remove(w))) onKeep();
  };
  return (
    <div className="card-text card-confirm">
      <p>
        {inside
          ? `“${w.name}” still has ${inside}. Move or delete them first, or archive the workspace instead.`
          : `Delete “${w.name}”? This can't be undone.`}
      </p>
      <div>
        <button type="button" className="ghost" disabled={busy} onClick={onKeep}>Keep</button>
        {!inside && <button type="button" className="ghost danger" disabled={busy} onClick={() => void remove()}>Delete</button>}
        {inside && !w.archived && <button type="button" className="ghost" onClick={() => void act.archive(w, true)}>Archive</button>}
      </div>
    </div>
  );
}

// A workspace card: the card is the link, and its ⋯ menu sits beside it rather than inside it. An
// archived workspace lists as a row with Restore.
function WorkspaceItem({ w, act }: { w: Workspace; act: Actions }) {
  const [mode, setMode] = useState<Mode>("view");
  const done = () => setMode("view");
  return (
    <div className={w.archived ? "ws-card ws-row" : "recent-card ws-card"}>
      {mode === "view" ? (
        <a className="ws-link" {...linkProps({ view: "project", ws: w.id })}>
          <Cover w={w} />
          <span className="card-text">
            <b>{w.name}</b>
            <small>
              {plural(w.videoCount, "video")} · {plural(w.assetCount, "asset")}
            </small>
          </span>
        </a>
      ) : (
        <div className="ws-link">
          <Cover w={w} />
          {mode === "rename" ? <RenameForm w={w} rename={act.rename} onDone={done} /> : <DeleteConfirm w={w} act={act} onKeep={done} />}
        </div>
      )}
      {mode === "view" && !BUILTIN_WORKSPACES.has(w.id) && (
        <div className="ws-actions">
          {w.archived && (
            <button type="button" className="ghost" onClick={() => void act.archive(w, false)}>
              Restore
            </button>
          )}
          <WorkspaceMenu w={w} onPick={(pick) => (pick === "archive" ? void act.archive(w, true) : setMode(pick))} />
        </div>
      )}
    </div>
  );
}

function NewProject({ first = false }: { first?: boolean }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      const created = await v3api.createWorkspace(trimmed);
      toast(`Created “${created.name}”`);
      navigate({ view: "project", ws: created.id });
    } catch (err) {
      toast(`Couldn't create the workspace: ${errorText(err)}`);
      setBusy(false);
    }
  };

  const caption = (
    <span className="card-text">
      <b>{first ? "Create your first Workspace" : "New workspace"}</b>
      <small>Videos in a workspace share one library</small>
    </span>
  );
  if (!open) {
    return (
      <button className={`recent-card new${first ? " first-workspace" : ""}`} onClick={() => setOpen(true)}>
        <span className="cover cover-new" aria-hidden="true">＋</span>
        {caption}
      </button>
    );
  }
  return (
    <form className={`recent-card new${first ? " first-workspace" : ""}`} onSubmit={submit}>
      <span className="cover cover-new">
        <input
          autoFocus
          aria-label="Workspace name"
          placeholder="Workspace name"
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setOpen(false);
              setName("");
            }
          }}
        />
        <button className="primary" disabled={!name.trim() || busy}>
          {busy ? "Creating…" : "Create"}
        </button>
      </span>
      {caption}
    </form>
  );
}

const SETUP_KEY = "cg3-dismissed-setup";

function WelcomeSetup() {
  const { load, update, reload } = useLoad(addonSnapshot, "welcome-addons");
  const refresh = async () => { const data = await addonSnapshot(); update(() => data); };
  const installing = load.state === "ready" && load.data.addons.some((addon) => addon.state === "installing");
  useEffect(() => {
    if (!installing) return;
    let live = true;
    const timer = window.setInterval(() => {
      void addonSnapshot().then((data) => live && update(() => data), () => {});
    }, 2000);
    return () => { live = false; window.clearInterval(timer); };
  }, [installing, update]);
  const [dismissed, setDismissed] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(SETUP_KEY) ?? "[]");
      return Array.isArray(saved) ? saved.filter((name): name is string => typeof name === "string") : [];
    } catch { return []; }
  });
  const dismiss = (name: string) => {
    const next = [...dismissed, name];
    setDismissed(next);
    try { localStorage.setItem(SETUP_KEY, JSON.stringify(next)); } catch { /* Storage may be unavailable in private browsing. */ }
  };
  return <section className="welcome-setup" aria-label="Optional setup">
    <h2>Make it yours <small>Optional setup</small></h2>
    <p className="muted">Start editing a script Video now. Add these whenever you need them in Settings, Add-ons.</p>
    {load.state === "loading" && <p className="muted">Checking optional add-ons…</p>}
    {load.state === "error" && <p role="status">Couldn't check optional add-ons. <button type="button" className="ghost" onClick={reload}>Retry</button></p>}
    {load.state === "ready" && <div className="welcome-cards">
      {load.data.addons.filter((addon) => ["transcription", "assistant"].includes(addon.name) && !dismissed.includes(addon.name)).map((addon) =>
        <AddonCard key={addon.name} addon={addon} recommended={load.data.recommendedTier} refresh={refresh}
          packaged={load.data.packaged} preview onDismiss={() => dismiss(addon.name)} />)}
    </div>}
  </section>;
}

function VideoCover({ v }: { v: RecentVideo }) {
  const [broken, setBroken] = useState(false);
  const placeholder = (
    <span className="cover cover-ph" style={accent(v.slug)} aria-hidden="true">
      {(v.title || v.slug).trim().charAt(0).toUpperCase() || "·"}
    </span>
  );
  if (v.script || v.thumbnail) return <ScriptCover video={v.slug} fallback={placeholder} />;
  if (v.thumbUrl && !broken) {
    return <img className="cover" src={v.thumbUrl} alt="" loading="lazy" onError={() => setBroken(true)} />;
  }
  return placeholder;
}

// Recently edited videos open in Story from a short row below the workspaces.
// Videos from archived workspaces are excluded.
function RecentVideos({ hidden }: { hidden: ReadonlySet<string> }) {
  const { load } = useLoad(() => v3api.recentVideos(8), "recent-videos");
  const shown = load.state === "ready" ? load.data.filter((v) => !hidden.has(v.workspaceId)).slice(0, RECENT_SHOWN) : [];
  if (!shown.length) return null;
  return (
    <section aria-labelledby="v3-recent-videos">
      <h2 className="recent-h" id="v3-recent-videos">Recent</h2>
      <div className="recent recent-row">
        {shown.map((v) => (
          <a key={v.slug} className="recent-card" {...linkProps({ view: "story", ws: v.workspaceId, video: v.slug })}>
            <VideoCover v={v} />
            <span className="card-text">
              <b>{v.title || v.slug}</b>
              <small>{[v.workspaceName, v.durationS != null ? clock(v.durationS) : ""].filter(Boolean).join(" · ")}</small>
            </span>
          </a>
        ))}
      </div>
    </section>
  );
}

type Start = "footage" | "idea";

// Home's starts have no workspace open yet, so they ask which one first, then hand off to the same
// overlay the Workspace page's "New video" menu opens (NewVideoFlow / NewScriptSheet, mounted once in V3App's Shell).
function ProjectPick({ start, projects, onClose }: { start: Start; projects: Workspace[]; onClose: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const picked = projects.filter((w) => !BUILTIN_WORKSPACES.has(w.id) && !w.archived);

  const openFor = (wsId: string) => {
    onClose();
    if (start === "footage") useNewVideoFlow.getState().openPicker(wsId);
    else useNewScript.getState().openSheet(wsId, "idea");
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      const created = await v3api.createWorkspace(trimmed);
      openFor(created.id);
    } catch (err) {
      toast(`Couldn't create the workspace: ${errorText(err)}`);
      setBusy(false);
    }
  };

  return (
    <div className="nv-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="nv-card" role="dialog" aria-modal="true" aria-label="Which workspace is this video for?">
        <header>
          <div>
            <p className="eyebrow">{start === "footage" ? "From footage" : "From an idea"}</p>
            <h2>Which workspace is this for?</h2>
          </div>
          <button type="button" className="ghost" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        {picked.length > 0 && (
          <div className="nv-library">
            {picked.map((w) => (
              <button key={w.id} type="button" className="nv-asset" onClick={() => openFor(w.id)}>
                <Cover w={w} />
                <span>{w.name}</span>
              </button>
            ))}
          </div>
        )}
        <form className="nv-confirm" onSubmit={create}>
          <label>
            New workspace
            <input value={name} maxLength={120} placeholder="Workspace name" onChange={(e) => setName(e.target.value)} />
          </label>
          <div className="nv-confirm-row">
            <button type="submit" className="primary" disabled={!name.trim() || busy}>
              {busy ? "Creating…" : "Create & continue"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// Workspaces appear first, followed by the two ways to start a
// video, compact, then a short Recent row.
export default function Home() {
  const { load, reload, update } = useLoad(v3api.workspaces, "workspaces");
  const [pick, setPick] = useState<Start | null>(null);
  const [sort, setSort] = useState<Sort>(savedSort);
  const [showArchived, setShowArchived] = useState(false);
  const ordered = load.state === "ready" ? sorted(load.data, sort) : [];
  const archived = ordered.filter((w) => w.archived);
  const firstRun = load.state === "ready" && !load.data.some((w) => !BUILTIN_WORKSPACES.has(w.id) || w.videoCount > 0 || w.assetCount > 0);
  const begin = (start: Start) => (load.state === "ready" ? setPick(start) : toast("Still loading your workspaces…"));
  const patch = (id: string, fields: Partial<Workspace>) => update((all) => all.map((w) => (w.id === id ? { ...w, ...fields } : w)));

  const act: Actions = {
    rename: async (w, name) => {
      try {
        const saved = await v3api.renameWorkspace(w.id, name);
        patch(w.id, { name: saved.name, lastUsedAt: saved.updatedAt });
        workspacesChanged();
        return true;
      } catch (err) {
        toast(`Couldn't rename: ${errorText(err)}`);
        return false;
      }
    },
    archive: async (w, next) => {
      try {
        await v3api.archiveWorkspace(w.id, next);
        patch(w.id, { archived: next });
        workspacesChanged();
        if (next) toast(`Archived “${w.name}”`, { label: "Undo", run: () => void act.archive(w, false) });
        else toast(`Restored “${w.name}”`);
      } catch (err) {
        toast(`Couldn't ${next ? "archive" : "restore"} “${w.name}”: ${errorText(err)}`);
      }
    },
    remove: async (w) => {
      try {
        await v3api.deleteWorkspace(w.id);
        update((all) => all.filter((x) => x.id !== w.id));
        workspacesChanged();
        toast(`Deleted “${w.name}”`);
        return true;
      } catch (err) {
        toast(err instanceof ApiError && err.reason ? err.reason : `Couldn't delete “${w.name}”: ${errorText(err)}`);
        return false;
      }
    },
  };

  return (
    <section className="home">
      <div className="home-inner">
        <p className="eyebrow">Cutgraph Studio</p>
        <Updates compact />
        <h1>{firstRun ? "Welcome to Cutgraph" : "What are we making?"}</h1>
        {firstRun && <p className="welcome-intro">Turn footage or a written script into a Video, then edit the story through its words.</p>}

        <section className="home-ws" aria-labelledby="v3-projects">
          <div className="home-h">
            <h2 className="recent-h" id="v3-projects">Workspaces</h2>
            {load.state === "ready" && !firstRun && (
              <>
                <label className="ws-sort">
                  Sort
                  <select aria-label="Sort workspaces" value={sort} onChange={(e) => {
                    const next = e.target.value as Sort;
                    setSort(next);
                    saveSort(next);
                  }}>
                    {SORTS.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
                {archived.length > 0 && (
                  <div className="pills">
                    <button type="button" className={showArchived ? "on" : undefined} aria-pressed={showArchived} onClick={() => setShowArchived((s) => !s)}>
                      Show archived <small>{archived.length}</small>
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
          {load.state === "loading" && <p className="muted">Loading workspaces…</p>}
          {load.state === "error" && <ErrorState error={load.error} what="Workspaces" onRetry={reload} />}
          {load.state === "ready" && (
            <div className="recent">
              {ordered.filter((w) => !w.archived && !firstRun).map((w) => (
                <WorkspaceItem key={w.id} w={w} act={act} />
              ))}
              <NewProject first={firstRun} />
            </div>
          )}
          {showArchived && archived.length > 0 && (
            <section className="ws-archived" aria-labelledby="v3-archived">
              <h3 id="v3-archived">Archived</h3>
              <div className="ws-rows">
                {archived.map((w) => (
                  <WorkspaceItem key={w.id} w={w} act={act} />
                ))}
              </div>
            </section>
          )}
        </section>

        {firstRun && <WelcomeSetup />}

        <section aria-labelledby="v3-start">
          <h2 className="recent-h" id="v3-start">New video</h2>
          <div className="starts">
            <button className="start" onClick={() => begin("footage")}>
              <FootageArt />
              <span>
                <b>From footage</b>
                <small>Drop in a recording. It's transcribed and laid out as a story.</small>
              </span>
            </button>
            <button className="start" onClick={() => begin("idea")}>
              <IdeaArt />
              <span>
                <b>From an idea</b>
                <small>Write or paste a script. Optionally ask the assistant to help.</small>
              </span>
            </button>
          </div>
        </section>
        {pick && load.state === "ready" && <ProjectPick start={pick} projects={ordered} onClose={() => setPick(null)} />}

        <RecentVideos hidden={new Set(archived.map((w) => w.id))} />
      </div>
    </section>
  );
}
import Updates from "./Updates";
