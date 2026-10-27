// Rename, tag and delete a library asset with an inline editor inside its tile.
import { useEffect, useState, type FormEvent } from "react";
import { v3api, type Asset } from "../api";
import { errorText, plural } from "../common";
import { toast } from "../Toast";

type Mode = "view" | "rename" | "tag" | "delete";

function useAssetUsage(ws: string, assetId: string, when: boolean): number | null {
  const [videos, setVideos] = useState<number | null>(null);
  useEffect(() => {
    setVideos(null);
    if (!when) return;
    let live = true;
    v3api.assetUsage(ws, assetId).then((u) => live && setVideos(u.videos), () => {});
    return () => {
      live = false;
    };
  }, [ws, assetId, when]);
  return videos;
}

function deleteNote(name: string, videos: number | null): string {
  if (videos === null) return `Delete “${name}”?`;
  if (!videos) return `Not used in any video. Delete “${name}”?`;
  return `Used in ${plural(videos, "video")}; deleting removes it there too. Delete anyway?`;
}

/** A tile's Rename / Tag / Delete, inline under its caption. `onChange` and `onDelete` update the
 * caller's own list — this component holds no list state of its own. */
export default function AssetActions({ ws, asset, onChange, onDelete }: {
  ws: string;
  asset: Asset;
  onChange: (updated: Asset) => void;
  onDelete: () => void;
}) {
  const [mode, setMode] = useState<Mode>("view");
  const [name, setName] = useState(asset.name);
  const [tags, setTags] = useState(asset.tags.join(", "));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const usage = useAssetUsage(ws, asset.id, mode === "delete");

  useEffect(() => {
    if (mode !== "view") return;
    setName(asset.name);
    setTags(asset.tags.join(", "));
    setError(null);
  }, [mode, asset.name, asset.tags]);

  async function rename(e: FormEvent): Promise<void> {
    e.preventDefault();
    const next = name.trim();
    if (!next || next === asset.name) {
      setMode("view");
      return;
    }
    setBusy(true);
    try {
      onChange(await v3api.updateAsset(ws, asset.id, { name: next }));
      setMode("view");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveTags(e: FormEvent): Promise<void> {
    e.preventDefault();
    const next = [...new Set(tags.split(",").map((t) => t.trim()).filter(Boolean))];
    setBusy(true);
    try {
      onChange(await v3api.updateAsset(ws, asset.id, { tags: next }));
      setMode("view");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(): Promise<void> {
    setBusy(true);
    try {
      await v3api.deleteAsset(ws, asset.id);
      toast(`Deleted “${asset.name}”.`);
      onDelete();
    } catch (err) {
      toast(`Couldn't delete: ${errorText(err)}`);
      setBusy(false);
    }
  }

  if (mode === "rename") {
    return (
      <form className="asset-edit" onSubmit={(e) => void rename(e)}>
        <input autoFocus value={name} maxLength={200} aria-label={`Rename ${asset.name}`} onChange={(e) => setName(e.target.value)} />
        {error && <p className="form-error" role="alert">{error}</p>}
        <div>
          <button type="button" className="ghost" disabled={busy} onClick={() => setMode("view")}>Cancel</button>
          <button type="submit" className="primary" disabled={busy || !name.trim()}>Save</button>
        </div>
      </form>
    );
  }
  if (mode === "tag") {
    return (
      <form className="asset-edit" onSubmit={(e) => void saveTags(e)}>
        <input autoFocus value={tags} placeholder="e.g. sfx" aria-label={`Tags for ${asset.name}, comma separated`} onChange={(e) => setTags(e.target.value)} />
        {error && <p className="form-error" role="alert">{error}</p>}
        <div>
          <button type="button" className="ghost" disabled={busy} onClick={() => setMode("view")}>Cancel</button>
          <button type="submit" className="primary" disabled={busy}>Save</button>
        </div>
      </form>
    );
  }
  if (mode === "delete") {
    return (
      <div className="asset-confirm">
        <p>{deleteNote(asset.name, usage)}</p>
        <div>
          <button type="button" className="ghost" disabled={busy} onClick={() => setMode("view")}>Keep</button>
          <button type="button" className="ghost danger" disabled={busy} onClick={() => void remove()}>Delete</button>
        </div>
      </div>
    );
  }
  return (
    <div className="asset-actions">
      <button type="button" onClick={() => setMode("rename")}>Rename</button>
      <button type="button" onClick={() => setMode("tag")}>Tag</button>
      <button type="button" className="danger" onClick={() => setMode("delete")}>Delete</button>
    </div>
  );
}
