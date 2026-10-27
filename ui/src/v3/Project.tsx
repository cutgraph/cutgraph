import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import {
  BUILTIN_WORKSPACES,
  dedupedCount,
  v3api,
  type Asset,
  type AssetKind,
  type BrandPatch,
  type VideoSummary,
  type Workspace,
  type WorkspaceDetail,
} from "./api";
import BrandField from "./BrandField";
import ScriptCover from "./ScriptCover";
import { ago, bytes, clock, ErrorState, errorText, plural, useLoad } from "./common";
import AssetActions from "./library/AssetActions";
import { OUR_BRAND } from "./library/cast";
import { reloadCasts, setWorkspaceBrand } from "./library/castStore";
import { useNewScript } from "./newscript/store";
import { useNewVideoFlow } from "./newvideo/store";
import { workspacesChanged } from "./ProjectSwitcher";
import { linkProps } from "./router";
import { toast } from "./Toast";

type Filter = "all" | AssetKind;

const FILTERS: [Filter, string][] = [["all", "All"], ["video", "Video"], ["image", "Image"], ["audio", "Audio"]];
const KIND_ICON: Record<AssetKind, string> = { video: "▶", image: "◩", audio: "♪" };

function NameField({ value, editable, onSave }: { value: string; editable: boolean; onSave: (name: string) => Promise<boolean> }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  if (!editable) {
    return (
      <h1 className="pname-h">
        <span className="pname">{value}</span>
      </h1>
    );
  }
  const commit = () => {
    const next = draft.trim();
    if (next && next !== value) void onSave(next).then((ok) => ok || setDraft(value));
    else setDraft(value);
  };
  return (
    <h1 className="pname-h" data-value={draft}>
      <input
        className="pname"
        aria-label="Workspace name"
        value={draft}
        size={1}
        maxLength={120}
        spellCheck={false}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
          if (e.key === "Escape") setDraft(value);
        }}
      />
    </h1>
  );
}

function NewVideoMenu({ ws }: { ws: string }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);
  return (
    <div className="menu-wrap" ref={wrap}>
      <button className="primary" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        New video
      </button>
      {open && (
        <div className="menu" role="menu">
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              useNewVideoFlow.getState().openPicker(ws);
            }}
          >
            <b>From footage</b>
            <small>Transcribe a recording and lay it out as a story</small>
          </button>
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              useNewScript.getState().openSheet(ws, "script");
            }}
          >
            <b>From a script</b>
            <small>Write it with Claude, starring Lens</small>
          </button>
        </div>
      )}
    </div>
  );
}

// move a video (from Unsorted or another workspace) into a different one. Reuses the PUT
// /api/workspaces/{id}/videos/{slug} route NewVideoFlow already assigns a fresh video's workspace with.
function MoveVideoMenu({ ws, slug, onMoved }: { ws: string; slug: string; onMoved: () => void }) {
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<Workspace[] | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  useEffect(() => {
    if (!open || projects) return;
    v3api.workspaces().then(setProjects, () => setProjects([]));
  }, [open, projects]);

  const destinations = (projects ?? []).filter((p) => p.id !== ws && !BUILTIN_WORKSPACES.has(p.id) && !p.archived);

  async function move(targetWs: string, targetName: string): Promise<void> {
    setOpen(false);
    try {
      await v3api.assignVideo(targetWs, slug);
      toast(`Moved to ${targetName}`);
      onMoved();
    } catch (err) {
      toast(`Couldn't move: ${errorText(err)}`);
    }
  }

  return (
    <div className="menu-wrap" ref={wrap}>
      <button
        type="button"
        className="ghost vrow-move"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.preventDefault();
          setOpen((o) => !o);
        }}
      >
        Move…
      </button>
      {open && (
        <div className="menu" role="menu">
          {ws !== "unsorted" && (
            <button role="menuitem" onClick={() => void move("unsorted", "Unsorted")}>
              Unsorted
            </button>
          )}
          {destinations.map((p) => (
            <button key={p.id} role="menuitem" onClick={() => void move(p.id, p.name)}>
              {p.name}
            </button>
          ))}
          {projects && !destinations.length && ws === "unsorted" && <span className="menu-empty">No other workspaces yet</span>}
        </div>
      )}
    </div>
  );
}

function VideoRow({ ws, v, onMoved }: { ws: string; v: VideoSummary; onMoved: (slug: string) => void }) {
  const meta = [v.durationS != null ? clock(v.durationS) : "", v.updatedAt ? `updated ${ago(v.updatedAt)}` : ""].filter(Boolean);
  return (
    <div className="vrow">
      <a className="vrow-link" {...linkProps({ view: "story", ws, video: v.slug })}>
        <i aria-hidden="true">{v.thumbnail ? <ScriptCover video={v.slug} fallback="▶" /> : "▶"}</i>
        <span>
          <b>{v.title || v.slug}</b>
          <small>{meta.join(" · ")}</small>
        </span>
      </a>
      <MoveVideoMenu ws={ws} slug={v.slug} onMoved={() => onMoved(v.slug)} />
    </div>
  );
}

function assetMeta(a: Asset): string {
  const size = a.width && a.height ? `${a.width}×${a.height}` : "";
  const length = a.durationS != null ? clock(a.durationS) : "";
  return [length, size].filter(Boolean).join(" · ") || bytes(a.bytes);
}

function AssetTile({ ws, a, onChange, onDelete }: { ws: string; a: Asset; onChange: (a: Asset) => void; onDelete: () => void }) {
  const [thumb, setThumb] = useState(a.kind !== "audio");
  return (
    <figure className={`asset k-${a.kind}`}>
      {thumb ? (
        <img src={v3api.assetThumbUrl(ws, a.id)} alt="" loading="lazy" onError={() => setThumb(false)} />
      ) : (
        <span className="asset-ic" aria-hidden="true">{KIND_ICON[a.kind]}</span>
      )}
      <em>{a.kind}</em>
      <figcaption>
        <b title={a.name}>{a.name}</b>
        <small>{assetMeta(a)}</small>
        {a.tags.length > 0 && (
          <span className="tags">
            {a.tags.map((t) => (
              <span key={t}>{t}</span>
            ))}
          </span>
        )}
        <AssetActions ws={ws} asset={a} onChange={onChange} onDelete={onDelete} />
      </figcaption>
    </figure>
  );
}

function DropZone({ busy, onFiles }: { busy: boolean; onFiles: (files: File[]) => void }) {
  const [over, setOver] = useState(false);
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (!busy) onFiles(Array.from(e.dataTransfer.files));
  };
  return (
    <div
      className={`drop${over ? " over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false);
      }}
      onDrop={onDrop}
    >
      <span className="drop-ic" aria-hidden="true">⤓</span>
      <p>
        <b>{busy ? "Importing…" : "Drop footage, photos or music"}</b>
        <small>Imported once and reusable in every video in this workspace. Duplicates are skipped.</small>
      </p>
      <label className={`ghost${busy ? " disabled" : ""}`}>
        Choose files
        <input
          className="sr-only"
          type="file"
          multiple
          accept="video/*,image/*,audio/*"
          disabled={busy}
          onChange={(e) => {
            onFiles(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
      </label>
    </div>
  );
}

function mergeAssets(current: Asset[], incoming: Asset[]): Asset[] {
  const fresh = new Map(incoming.map((a) => [a.id, a]));
  const kept = current.map((a) => fresh.get(a.id) ?? a);
  const known = new Set(current.map((a) => a.id));
  return [...incoming.filter((a) => !known.has(a.id)), ...kept];
}

function ProjectView({ id, detail, update }: { id: string; detail: WorkspaceDetail; update: (fn: (d: WorkspaceDetail) => WorkspaceDetail) => void }) {
  const { workspace, videos, assets } = detail;
  const [filter, setFilter] = useState<Filter>("all");
  const [uploading, setUploading] = useState(false);
  const builtin = BUILTIN_WORKSPACES.has(id);
  const shown = filter === "all" ? assets : assets.filter((a) => a.kind === filter);
  const count = (f: Filter) => (f === "all" ? assets.length : assets.filter((a) => a.kind === f).length);

  const rename = async (name: string) => {
    try {
      const saved = await v3api.renameWorkspace(id, name);
      update((d) => ({ ...d, workspace: { ...d.workspace, ...saved } }));
      void reloadCasts();
      workspacesChanged();
      return true;
    } catch (err) {
      toast(`Couldn't rename: ${errorText(err)}`);
      return false;
    }
  };

  const saveBrand = useCallback(async (patch: BrandPatch) => {
    try {
      const saved = await setWorkspaceBrand(id, patch);
      update((d) => ({ ...d, workspace: { ...d.workspace, ...saved } }));
      return true;
    } catch (err) {
      toast(`Couldn't save the brand: ${errorText(err)}`);
      return false;
    }
  }, [id, update]);

  const videoMoved = (slug: string) => {
    update((d) => ({ ...d, videos: d.videos.filter((v) => v.slug !== slug) }));
  };

  const assetChanged = (updated: Asset) => {
    update((d) => ({ ...d, assets: d.assets.map((a) => (a.id === updated.id ? updated : a)) }));
  };

  const assetDeleted = (assetId: string) => {
    update((d) => {
      const remaining = d.assets.filter((a) => a.id !== assetId);
      return { ...d, assets: remaining, workspace: { ...d.workspace, assetCount: remaining.length } };
    });
  };

  const upload = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    try {
      const result = await v3api.uploadAssets(id, files);
      const known = new Set(assets.map((a) => a.id));
      const added = result.assets.filter((a) => !known.has(a.id)).length;
      const skipped = dedupedCount(result);
      update((d) => {
        const merged = mergeAssets(d.assets, result.assets);
        return { ...d, assets: merged, workspace: { ...d.workspace, assetCount: merged.length } };
      });
      toast(`Added ${plural(added, "asset")}${skipped ? ` · ${skipped} already in the library` : ""}`);
    } catch (err) {
      toast(`Import failed: ${errorText(err)}`);
    } finally {
      setUploading(false);
    }
  };

  return (
    <section className="library proj">
      <header className="lib-top">
        <div className="ptitle">
          <NameField value={workspace.name} editable={!builtin} onSave={rename} />
          <span className="kind">
            {plural(videos.length, "video")} · {plural(assets.length, "asset")}
            {workspace.archived && " · Archived"}
          </span>
        </div>
      </header>

      <section className="lib-sec" aria-labelledby="v3-brand">
        <div className="sec-head">
          <h2 id="v3-brand">Brand</h2>
        </div>
        <BrandField brand={workspace.brand ?? OUR_BRAND} editable={id !== "unsorted"} onSave={saveBrand} />
        <p className="muted brand-note">
          {id === "unsorted" ? "Unsorted videos carry the brand kit's eyebrow." : "Above every animated headline in this workspace's videos. A shot can change or hide it."}
        </p>
      </section>

      <section className="lib-sec" aria-labelledby="v3-videos">
        <div className="sec-head">
          <h2 id="v3-videos">
            Videos <small>{videos.length}</small>
          </h2>
          <NewVideoMenu ws={id} />
        </div>
        {videos.length ? (
          <div className="vlist">
            {videos.map((v) => (
              <VideoRow key={v.slug} ws={id} v={v} onMoved={videoMoved} />
            ))}
          </div>
        ) : (
          <p className="empty">No videos yet. Start one with New video.</p>
        )}
      </section>

      <section className="lib-sec" aria-labelledby="v3-library">
        <div className="sec-head">
          <h2 id="v3-library">
            Library <small>{assets.length}</small>
          </h2>
          <div className="pills" role="group" aria-label="Filter by kind">
            {FILTERS.map(([f, label]) => (
              <button key={f} className={filter === f ? "on" : undefined} aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {label} <small>{count(f)}</small>
              </button>
            ))}
          </div>
        </div>
        {id !== "unsorted" && <DropZone busy={uploading} onFiles={upload} />}
        {shown.length ? (
          <div className="agrid">
            {shown.map((a) => (
              <AssetTile key={a.id} ws={id} a={a} onChange={assetChanged} onDelete={() => assetDeleted(a.id)} />
            ))}
          </div>
        ) : (
          <p className="empty">
            {assets.length ? `No ${filter} assets in this workspace.` : "The library is empty. Drop files above to import them."}
          </p>
        )}
      </section>
    </section>
  );
}

export default function Project({ id }: { id: string }) {
  const { load, reload, update } = useLoad(() => v3api.workspace(id), id);
  if (load.state === "loading") {
    return (
      <section className="library proj">
        <p className="muted">Loading workspace…</p>
      </section>
    );
  }
  if (load.state === "error") {
    return (
      <section className="library proj">
        <header className="lib-top">
          <h1>Workspace</h1>
        </header>
        {load.error.status === 404 ? (
          <div className="empty notice" role="alert">
            <p>
              <b>This workspace doesn't exist, or workspaces aren't available on this studio yet.</b>
              <small>{load.error.message}</small>
            </p>
            <a className="ghost" {...linkProps({ view: "home" })}>
              Back to workspaces
            </a>
          </div>
        ) : (
          <ErrorState error={load.error} what="Workspaces" onRetry={reload} />
        )}
      </section>
    );
  }
  return <ProjectView id={id} detail={load.data} update={update} />;
}
