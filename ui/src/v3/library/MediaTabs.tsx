// The Library's other tabs: workspace media, graphic templates, and the brand kit.
import { useEffect, useState } from "react";
import { GRAPHIC_DEFINITIONS } from "../../graphics/registry";
import { BUILTIN_WORKSPACES, v3api, type Asset } from "../api";
import { clock } from "../common";
import { linkProps } from "../router";
import StockAttribution from "../StockAttribution";
import { assetTab } from "../story/drawer/library";
import { ClipThumb, GraphicFrame, templateItem } from "../story/drawer/Tiles";
import AssetActions from "./AssetActions";

export type MediaTab = "broll" | "music" | "sfx";

const BRAND = "brand";
const ICON: Record<MediaTab, string> = { broll: "▣", music: "♪", sfx: "✧" };
const NOTE: Record<MediaTab, string> = {
  broll: "Clips and photos, ready to drop on a word in any video.",
  music: "Tracks in the library. Tag a short sting “sfx” to file it under Sounds.",
  sfx: "Short stings in the library. Tag a long track “music” to file it under Music.",
};
const EMPTY: Record<MediaTab, string> = { broll: "No B-roll", music: "No music", sfx: "No sounds" };

interface MediaGroup {
  workspaceId: string;
  workspaceName: string;
  assets: Asset[];
}

/** The open project's library plus the brand's, each a separate group with the assets it owns —
 * unlike `useDrawerLibrary` (the Story drawer's flat pick list), the grouping itself is the point. */
function useMediaGroups(ws: string | undefined): { groups: MediaGroup[]; loading: boolean; error: string | null; refresh: () => void } {
  const projectId = ws && !BUILTIN_WORKSPACES.has(ws) ? ws : null;
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ groups: MediaGroup[]; loading: boolean; error: string | null }>({ groups: [], loading: true, error: null });
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    const ids = projectId ? [projectId, BRAND] : [BRAND];
    Promise.allSettled(ids.map((id) => v3api.workspace(id))).then((results) => {
      if (!live) return;
      const groups = results.flatMap((r) =>
        r.status === "fulfilled" ? [{ workspaceId: r.value.workspace.id, workspaceName: r.value.workspace.name, assets: r.value.assets }] : [],
      );
      const failed = results.find((r): r is PromiseRejectedResult => r.status === "rejected");
      setState({ groups, loading: false, error: failed && !groups.length ? String(failed.reason?.message ?? failed.reason) : null });
    });
    return () => {
      live = false;
    };
  }, [projectId, attempt]);
  return { ...state, refresh: () => setAttempt((n) => n + 1) };
}

function MediaTile({ ws, a, tab, onChange, onDelete }: {
  ws: string;
  a: Asset;
  tab: MediaTab;
  onChange: (a: Asset) => void;
  onDelete: () => void;
}) {
  const [hover, setHover] = useState(false);
  const meta = [a.durationS != null ? clock(a.durationS) : a.kind === "image" ? "Photo" : "", ...a.tags].filter(Boolean);
  const icon = <span className="asset-ic" aria-hidden="true">{ICON[tab]}</span>;
  const playable = a.kind === "video";
  return (
    <figure className={`asset k-${a.kind}`} onMouseEnter={playable ? () => setHover(true) : undefined} onMouseLeave={playable ? () => setHover(false) : undefined}>
      {a.kind === "audio"
        ? icon
        : <ClipThumb thumb={v3api.assetThumbUrl(ws, a.id)} clip={hover ? v3api.assetFileUrl(ws, a.id) : null} fallback={icon} />}
      <figcaption>
        <b title={a.name}>{a.name}</b>
        <small>{meta.join(" · ")}</small>
        {a.attribution && <StockAttribution attribution={a.attribution} kind={a.kind === "image" ? "photo" : "video"} />}
        <AssetActions ws={ws} asset={a} onChange={onChange} onDelete={onDelete} />
      </figcaption>
    </figure>
  );
}

function MediaGroupSection({ group, tab, heading, refresh }: { group: MediaGroup; tab: MediaTab; heading: string; refresh: () => void }) {
  const assets = group.assets.filter((a) => assetTab(a) === tab);
  return (
    <section className="lib-media-group">
      <h3>{heading}</h3>
      {assets.length ? (
        <div className="agrid">
          {assets.map((a) => (
            <MediaTile
              key={a.id}
              ws={group.workspaceId}
              a={a}
              tab={tab}
              onChange={refresh}
              onDelete={refresh}
            />
          ))}
        </div>
      ) : (
        <p className="muted empty-note">{EMPTY[tab]} here yet.</p>
      )}
    </section>
  );
}

export function BrandMedia({ tab, ws }: { tab: MediaTab; ws?: string }) {
  const { groups, loading, error, refresh } = useMediaGroups(ws);
  if (loading) return <p className="muted">Loading the library…</p>;
  if (error && !groups.length) {
    return (
      <div className="empty notice" role="alert">
        <p>
          <b>Couldn't load the library.</b>
          <small>{error}</small>
        </p>
      </div>
    );
  }
  const project = groups.find((g) => g.workspaceId !== BRAND) ?? null;
  const brand = groups.find((g) => g.workspaceId === BRAND) ?? null;
  return (
    <>
      <p className="muted lib-note">{NOTE[tab]}</p>
      {project && <MediaGroupSection group={project} tab={tab} heading={project.workspaceName} refresh={refresh} />}
      {brand && <MediaGroupSection group={brand} tab={tab} heading="Brand" refresh={refresh} />}
    </>
  );
}

export function GraphicTemplates() {
  return (
    <>
      <p className="muted lib-note">Templates you fill in from the Story drawer; every one is a transparent overlay.</p>
      <div className="lib-tiles">
        {GRAPHIC_DEFINITIONS.map((d) => (
          <figure key={d.id} className="gfx-tile">
            <GraphicFrame item={templateItem(d.id)} backdrop={null} className="tile-art" zoom />
            <figcaption>{d.name}</figcaption>
          </figure>
        ))}
      </div>
    </>
  );
}

const PALETTE: [string, string][] = [
  ["#f5c518", "Gold"], ["#111214", "Near-black"], ["#79d5df", "Cyan"], ["#90caa5", "Green"], ["#eef2f4", "Paper"],
];

export function BrandKit() {
  return (
    <div className="brand">
      <section className="lib-sec">
        <h2>Palette</h2>
        <div className="swatches">
          {PALETTE.map(([c, n]) => (
            <span key={c}>
              <i style={{ background: c }} />
              {n}
              <small>{c}</small>
            </span>
          ))}
        </div>
      </section>
      <section className="lib-sec">
        <h2>Type</h2>
        <p className="type-sample">Inter · titles and captions</p>
      </section>
      <section className="lib-sec">
        <h2>Rules</h2>
        <ul className="bullets">
          <li>Captions: the spoken word in yellow, the rest in white</li>
          <li>Animation lands on the narration word it illustrates</li>
        </ul>
      </section>
      <section className="lib-sec">
        <a className="ghost" {...linkProps({ view: "project", ws: BRAND })}>Open brand assets</a>
      </section>
    </div>
  );
}
