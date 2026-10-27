// "Search stock": Pexels + Pixabay search merged into one grid, preview on hover/click,
// "Add to library" downloads server-side into the current video's workspace library. The imported
// asset then flows through the ordinary drawer/library.ts + drawer/insert.ts path like any upload.

import { useState, type FormEvent } from "react";
import { ApiError, v3api, type StockItem, type StockKind } from "../../../api";
import { clock, errorText, useLoad } from "../../../common";
import { linkProps } from "../../../router";
import StockAttribution from "../../../StockAttribution";
import { toast } from "../../../Toast";
import "./stock.css";

type SearchKind = "all" | StockKind;

const KIND_LABEL: Record<SearchKind, string> = { all: "All", video: "Video", photo: "Photo" };

function dedupeById(items: StockItem[]): StockItem[] {
  const seen = new Set<string>();
  const out: StockItem[] = [];
  for (const item of items) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    out.push(item);
  }
  return out;
}

function StockThumb({ item }: { item: StockItem }) {
  const [hover, setHover] = useState(false);
  const showVideo = hover && item.kind === "video" && !!item.previewUrl;
  return (
    <span className="stock-thumb" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      {showVideo ? (
        <video src={item.previewUrl ?? undefined} muted autoPlay loop playsInline />
      ) : item.thumbUrl ? (
        <img src={item.thumbUrl} alt="" loading="lazy" draggable={false} />
      ) : (
        <span className="tile-ic" aria-hidden="true">{item.kind === "video" ? "▣" : "◧"}</span>
      )}
      {item.kind === "video" && item.durationS != null && <span className="stock-badge">{clock(item.durationS)}</span>}
    </span>
  );
}

function StockTile({ item, added, adding, onPreview, onAdd }: {
  item: StockItem;
  added: boolean;
  adding: boolean;
  onPreview: () => void;
  onAdd: () => void;
}) {
  const dims = item.width && item.height ? `${item.width}×${item.height}` : item.kind === "photo" ? "Photo" : "Video";
  return (
    <div className="tile stock-tile">
      <button type="button" className="stock-preview-btn" onClick={onPreview} aria-label={`Preview by ${item.author}`}>
        <StockThumb item={item} />
      </button>
      <StockAttribution attribution={item} kind={item.kind} />
      <small>{dims}</small>
      <button
        type="button"
        className={`ghost stock-add${added ? " on" : ""}`}
        disabled={adding || added}
        onClick={onAdd}
      >
        {added ? "Added" : adding ? "Adding…" : "Add to library"}
      </button>
    </div>
  );
}

function StockPreview({ item, added, adding, onClose, onAdd }: {
  item: StockItem;
  added: boolean;
  adding: boolean;
  onClose: () => void;
  onAdd: () => void;
}) {
  return (
    <div className="stock-lightbox" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="stock-lightbox-body" onClick={(e) => e.stopPropagation()}>
        {item.kind === "video" ? (
          <video className="cue-preview" src={item.previewUrl ?? item.downloadUrl} controls autoPlay muted loop playsInline />
        ) : (
          <img className="cue-preview" src={item.previewUrl ?? item.thumbUrl ?? item.downloadUrl} alt="" />
        )}
        <div className="stock-lightbox-meta">
          <StockAttribution attribution={item} kind={item.kind} />
          <small>{item.license}</small>
        </div>
        <div className="row-end">
          <button type="button" className="ghost" onClick={onClose}>Close</button>
          <button type="button" className="primary" disabled={adding || added} onClick={onAdd}>
            {added ? "Added to library" : adding ? "Adding…" : "Add to library"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function StockPanel({ workspaceId, onAdded, onClose }: {
  workspaceId: string;
  onAdded: () => void;
  onClose: () => void;
}) {
  const { load: providers } = useLoad(() => v3api.stockProviders(), "stock-providers");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<SearchKind>("all");
  const [items, setItems] = useState<StockItem[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [preview, setPreview] = useState<StockItem | null>(null);
  const [addingId, setAddingId] = useState<string | null>(null);
  const [addedIds, setAddedIds] = useState<Set<string>>(new Set());

  const runSearch = async (q: string, nextPage: number, append: boolean) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    setLoading(true);
    try {
      const result = await v3api.searchStock(trimmed, kind, nextPage);
      Object.values(result.errors).forEach((message) => toast(message));
      setItems((prev) => dedupeById(append ? [...prev, ...result.results] : result.results));
      setPage(result.page);
      setHasMore(result.hasMore);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Stock search failed.");
    } finally {
      setLoading(false);
      setSearched(true);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void runSearch(query, 1, false);
  };

  const add = async (item: StockItem) => {
    setAddingId(item.id);
    try {
      const result = await v3api.importStock(workspaceId, item);
      setAddedIds((prev) => new Set(prev).add(item.id));
      onAdded();
      toast(result.deduped ? `Already in your library: ${result.asset.name}` : `Added to library: ${result.asset.name}`);
      setPreview((p) => (p && p.id === item.id ? null : p));
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Couldn't add that to your library.");
    } finally {
      setAddingId(null);
    }
  };

  const configured = providers.state === "ready" && (providers.data.pexels.configured || providers.data.pixabay.configured);

  return (
    <div className="stock-panel">
      <div className="drawer-head stock-head">
        <button type="button" className="ghost stock-back" onClick={onClose} aria-label="Back to library">‹ Back</button>
        <div>
          <h3>Search stock</h3>
          <p className="muted">
            <a href="https://www.pexels.com" target="_blank" rel="noopener noreferrer">Photos and videos provided by Pexels</a>
            {" · "}<a href="https://pixabay.com" target="_blank" rel="noopener noreferrer">Pixabay</a>
          </p>
        </div>
      </div>

      {providers.state === "loading" ? (
        <p className="drawer-empty">Checking your keys…</p>
      ) : providers.state === "error" ? (
        <p className="drawer-empty">{errorText(providers.error)}</p>
      ) : !configured ? (
        <p className="drawer-empty">
          Add your Pexels or Pixabay key in{" "}
          <a className="link" {...linkProps({ view: "settings" })}>Settings</a> to search stock B-roll.
        </p>
      ) : (
        <>
          <form className="stock-search" onSubmit={submit}>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search stock video & photos…"
              aria-label="Search stock"
            />
            <button type="submit" className="primary" disabled={loading || !query.trim()}>Search</button>
          </form>
          <div className="pills stock-kind" role="tablist" aria-label="Result type">
            {(["all", "video", "photo"] as const).map((k) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={kind === k}
                className={kind === k ? "on" : undefined}
                onClick={() => setKind(k)}
              >
                {KIND_LABEL[k]}
              </button>
            ))}
          </div>

          {loading && !items.length ? (
            <p className="drawer-empty">Searching…</p>
          ) : searched && !items.length ? (
            <p className="drawer-empty">No results for "{query}".</p>
          ) : items.length > 0 ? (
            <>
              <div className="tiles stock-tiles">
                {items.map((item) => (
                  <StockTile
                    key={item.id}
                    item={item}
                    added={addedIds.has(item.id)}
                    adding={addingId === item.id}
                    onPreview={() => setPreview(item)}
                    onAdd={() => void add(item)}
                  />
                ))}
              </div>
              {hasMore && (
                <button type="button" className="ghost stock-more" disabled={loading} onClick={() => void runSearch(query, page + 1, true)}>
                  {loading ? "Loading…" : "More"}
                </button>
              )}
            </>
          ) : (
            <p className="drawer-empty">Search Pexels and Pixabay for B-roll.</p>
          )}
        </>
      )}

      {preview && (
        <StockPreview
          item={preview}
          added={addedIds.has(preview.id)}
          adding={addingId === preview.id}
          onClose={() => setPreview(null)}
          onAdd={() => void add(preview)}
        />
      )}
    </div>
  );
}
