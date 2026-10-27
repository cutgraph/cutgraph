// The Export sheet's Thumbnail section: a live preview of the video's thumbnail (16:9, or the 9:16
// cover), a PNG download of each, and the way into the Thumbnail editor.
import { useMemo, useState } from "react";
import type { PosterFormat } from "motion-core";
import { useProjectStore } from "../../../store/useProjectStore";
import { errorText } from "../../common";
import { useVideoCast } from "../../library/castStore";
import { openThumbnail } from "../storyStore";
import PosterPicture from "./PosterPicture";
import { downloadThumbnail, posterOf } from "./thumbnail";

const FORMATS: readonly PosterFormat[] = ["16:9", "9:16"];

export default function ThumbnailSection() {
  const project = useProjectStore((s) => s.project);
  const { cast } = useVideoCast();
  const [format, setFormat] = useState<PosterFormat>("16:9");
  const [busy, setBusy] = useState<PosterFormat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const poster = useMemo(() => (project ? posterOf(project, cast) : null), [project, cast]);
  if (!project || !poster) return null;

  const download = (f: PosterFormat): void => {
    setBusy(f);
    setError(null);
    downloadThumbnail(project.name, f).then(
      () => setBusy(null),
      (err: unknown) => {
        setBusy(null);
        setError(`The ${f} thumbnail didn't render: ${errorText(err)}`);
      },
    );
  };

  return (
    <section className="thumb" data-slot="thumbnail-section" aria-label="Thumbnail">
      <p className="eyebrow">Thumbnail</p>
      <div className="thumb-frame" data-format={format}>
        <PosterPicture poster={poster} format={format} width={format === "16:9" ? 792 : 338} label={`${format} thumbnail preview`} />
      </div>
      <div className="thumb-bar">
        <div className="pills" role="group" aria-label="Thumbnail format">
          {FORMATS.map((f) => (
            <button key={f} type="button" className={f === format ? "on" : undefined} aria-pressed={f === format} onClick={() => setFormat(f)}>
              {f}
            </button>
          ))}
        </div>
        <button type="button" className="ghost" onClick={() => openThumbnail(true)}>
          Edit thumbnail
        </button>
      </div>
      <div className="thumb-downloads">
        {FORMATS.map((f) => (
          <button key={f} type="button" className="ghost" disabled={busy !== null} onClick={() => download(f)}>
            {busy === f ? "Rendering…" : `Download PNG · ${f}`}
          </button>
        ))}
      </div>
      {error && <p className="thumb-status error" role="alert">{error}</p>}
      {!project.thumbnail && !error && (
        <p className="thumb-status">A starting point from the video's title and cast. Edit it to make it the video's cover.</p>
      )}
    </section>
  );
}
