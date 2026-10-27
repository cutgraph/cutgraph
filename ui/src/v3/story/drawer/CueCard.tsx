import { useRef, useState } from "react";
import type { TranscriptWord } from "../../../api";
import type { Project } from "../../../types/project";
import {
  GRAPHIC_DEFINITIONS,
  GRAPHIC_REGISTRY,
  isGraphicItem,
  type GraphicComponentId,
  type TrustedGraphicItem,
} from "../../../graphics/registry";
import { resolveMedia, WORKSPACE_MEDIA } from "../../../preview/resolveMedia";
import { playedF } from "../../../timeline/geometry";
import { sceneOfWord, type SceneSpan } from "../model";
import type { Cue, CueKind } from "./insert";
import { assetsForTab, type LibraryAsset } from "./library";
import { AssetTile, clock, GraphicFrame, GraphicTile, ICON, TYPE_NAME } from "./Tiles";

const SWAPS = 4;

export interface CueActions {
  close: () => void;
  remove: () => void;
  swapAsset: (entry: LibraryAsset) => void;
  swapGraphic: (componentId: GraphicComponentId) => void;
  editGraphic: (props: Record<string, unknown>) => void;
}

// The library asset a clip plays, when it came from one.
export function cueAsset(cue: Cue, assets: LibraryAsset[]): LibraryAsset | null {
  const match = cue.clip?.media.source.match(WORKSPACE_MEDIA);
  return match ? assets.find((e) => e.workspaceId === match[1] && e.asset.file === match[2]) ?? null : null;
}

export function cueStartsOn(cue: Cue, words: TranscriptWord[], spans: SceneSpan[], fps: number): string {
  const word = cue.wordIdx === null ? undefined : words[cue.wordIdx];
  if (!word || cue.wordIdx === null) return `starts at ${clock(cue.tlStartF / fps)}`;
  const scene = sceneOfWord(spans, cue.wordIdx);
  return `starts on “${word.w.trim()}”${scene ? ` in ${scene.scene.title}` : ""}`;
}

function AudioPreview({ url, kind, seconds }: { url: string; kind: CueKind; seconds: number }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  return (
    <div className={`cue-audio t-${kind}`}>
      <button
        type="button"
        className="play"
        aria-label={playing ? "Pause preview" : "Play preview"}
        onClick={() => { const a = audio.current; if (a?.paused) void a.play(); else a?.pause(); }}
      >
        {playing ? "❚❚" : "▶"}
      </button>
      <span className="tile-ic" aria-hidden="true">{ICON[kind]}</span>
      <span className="muted">{clock(seconds)} on the timeline</span>
      <audio ref={audio} src={url} preload="none" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
    </div>
  );
}

function Preview({ cue, graphic, backdrop, project }: { cue: Cue; graphic: TrustedGraphicItem | null; backdrop: string | null; project: Project }) {
  if (graphic) return <GraphicFrame item={graphic} backdrop={backdrop} className="cue-preview" />;
  const url = cue.clip ? resolveMedia(cue.clip.media) : null;
  if (!url || !cue.clip) return null;
  const { tlStartF, srcInF, srcOutF } = cue.clip;
  if (cue.kind !== "broll") return <AudioPreview url={url} kind={cue.kind} seconds={playedF(project, tlStartF, srcOutF - srcInF) / project.fps} />;
  return /\.(png|jpe?g|webp|gif)$/i.test(cue.clip.media.source)
    ? <img className="cue-preview" src={url} alt="" />
    : <video className="cue-preview" src={url} muted loop autoPlay playsInline />;
}

export default function CueCard({ cue, project, words, spans, assets, backdrop, actions }: {
  cue: Cue;
  project: Project;
  words: TranscriptWord[];
  spans: SceneSpan[];
  assets: LibraryAsset[];
  backdrop: string | null;
  actions: CueActions;
}) {
  const graphic = cue.item && isGraphicItem(cue.item) ? (cue.item as TrustedGraphicItem) : null;
  const entry = cueAsset(cue, assets);
  const spec = graphic ? GRAPHIC_REGISTRY[graphic.componentId] : null;
  const name = spec?.name ?? entry?.asset.name ?? cue.clip?.media.source.split("/").pop() ?? cue.id;
  const source = entry?.workspaceName;
  const props = (graphic?.props ?? {}) as unknown as Record<string, unknown>;
  const fields = spec ? Object.entries(spec.propSchema).filter(([, kind]) => kind === "text").map(([field]) => field) : [];
  const swaps = graphic
    ? GRAPHIC_DEFINITIONS.filter((d) => d.id !== graphic.componentId).slice(0, SWAPS).map((d) => (
      <GraphicTile key={d.id} componentId={d.id} draggable={false} onPick={() => actions.swapGraphic(d.id)} />
    ))
    : cue.kind === "graphic" ? [] : assetsForTab(assets, cue.kind).filter((e) => e !== entry).slice(0, SWAPS).map((e) => (
      <AssetTile key={`${e.workspaceId}/${e.asset.id}`} entry={e} draggable={false} onPick={() => actions.swapAsset(e)} />
    ));

  return (
    <div className="cue-card" data-cue={cue.id}>
      <div className="cue-card-head">
        <span className={`cue cue-${cue.kind}`}>{ICON[cue.kind]} {TYPE_NAME[cue.kind]}</span>
        <button type="button" className="icon" aria-label="Back to library" title="Back to library" onClick={actions.close}>×</button>
      </div>
      <Preview cue={cue} graphic={graphic} backdrop={backdrop} project={project} />
      <h3>{name}</h3>
      <p className="muted">{source ? `${source} · ` : ""}{cueStartsOn(cue, words, spans, project.fps)}</p>
      {fields.map((field) => {
        const value = String(props[field] ?? "");
        return (
          <label className="cue-field" key={field}>
            <span>{field}</span>
            <input
              key={`${cue.id}:${value}`}
              defaultValue={value}
              aria-label={`Graphic ${field}`}
              onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
              onBlur={(event) => { if (event.currentTarget.value !== value) actions.editGraphic({ ...props, [field]: event.currentTarget.value }); }}
            />
          </label>
        );
      })}
      {swaps.length > 0 && <p className="eyebrow">Swap for</p>}
      {swaps.length > 0 && <div className="tiles">{swaps}</div>}
      <div className="row-end">
        <button type="button" className="ghost danger" onClick={actions.remove}>Remove</button>
      </div>
    </div>
  );
}
