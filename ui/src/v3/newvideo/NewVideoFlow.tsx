import { useEffect, useState } from "react";
import { v3api, type Asset } from "../api";
import { errorText, useLoad } from "../common";
import ProcessingCard from "./ProcessingCard";
import { useNewVideoFlow } from "./store";
import { TranscriptionRequired } from "../Addons";
import { useRoute } from "../router";
import "./NewVideoFlow.css";

const stem = (name: string) => name.replace(/\.[^/.]+$/, "");

function FilePick({ onPick }: { onPick: (file: File) => void }) {
  const [over, setOver] = useState(false);
  return (
    <label
      className={`nv-drop${over ? " over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const file = e.dataTransfer.files[0];
        if (file) onPick(file);
      }}
    >
      <span className="nv-drop-ic" aria-hidden="true">
        ⤓
      </span>
      <span>
        <b>Drop a video here</b>
      </span>
      <small>or choose a file · MP4, MOV, MKV, AVI, WebM, M4V</small>
      <input
        className="sr-only"
        type="file"
        aria-label="Choose a video file"
        accept="video/*,.m4v"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onPick(file);
          e.target.value = "";
        }}
      />
    </label>
  );
}

function LibraryPick({ wsId, onPick }: { wsId: string; onPick: (asset: Asset) => void }) {
  const { load } = useLoad(() => v3api.workspace(wsId), wsId);
  if (load.state === "loading") return <p className="muted">Loading your library…</p>;
  if (load.state === "error") return <p className="muted">Couldn&apos;t load the library: {errorText(load.error)}</p>;
  const videos = load.data.assets.filter((a) => a.kind === "video");
  if (!videos.length) return <p className="muted">No video assets in this workspace&apos;s library yet — choose a file instead.</p>;
  return (
    <div className="nv-library">
      {videos.map((a) => (
        <button key={a.id} type="button" className="nv-asset" onClick={() => onPick(a)}>
          <img src={v3api.assetThumbUrl(wsId, a.id)} alt="" loading="lazy" />
          <span>{a.name}</span>
        </button>
      ))}
    </div>
  );
}

const BUSY_PHASES = new Set(["uploading", "creating", "transcribing", "scenes"]);

export default function NewVideoFlow() {
  const route = useRoute();
  const { open, wsId, source, flow, pickSource, setTitle, confirm, retry, close, reconcile } = useNewVideoFlow();
  const [tab, setTab] = useState<"file" | "library">("file");

  useEffect(() => reconcile(), [reconcile]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  if (!open || route.view === "settings") return null;
  const busy = BUSY_PHASES.has(flow.phase);

  return (
    <div className="nv-overlay" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      {flow.phase === "idle" ? (
        <div className="nv-card" role="dialog" aria-modal="true" aria-label="New video from footage">
          <header>
            <div>
              <p className="eyebrow">From footage</p>
              <h2>Add your footage</h2>
            </div>
            <button type="button" className="ghost" aria-label="Close" onClick={close}>
              ×
            </button>
          </header>
          <TranscriptionRequired><div className="pills nv-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={tab === "file"} className={tab === "file" ? "on" : undefined} onClick={() => setTab("file")}>
              Choose a file
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "library"}
              className={tab === "library" ? "on" : undefined}
              onClick={() => setTab("library")}
            >
              From your library
            </button>
          </div>
          {tab === "file" ? (
            <FilePick onPick={(file) => pickSource({ kind: "upload", file }, stem(file.name))} />
          ) : (
            <LibraryPick wsId={wsId} onPick={(asset) => pickSource({ kind: "library", assetId: asset.id, name: asset.name }, stem(asset.name))} />
          )}
          {source && (
            <div className="nv-confirm">
              <p className="muted">{source.kind === "upload" ? source.file.name : source.name}</p>
              <label>
                Title
                <input type="text" value={flow.title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
              </label>
              <div className="nv-confirm-row">
                <button type="button" className="primary" disabled={!flow.title.trim()} onClick={confirm}>
                  Create video
                </button>
              </div>
            </div>
          )}
          </TranscriptionRequired>
        </div>
      ) : (
        <div className="nv-card">
          <ProcessingCard state={flow} onRetry={retry} />
          {!busy && (
            <div className="nv-confirm-row">
              <button type="button" className="ghost" onClick={close}>
                Close
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
