import { useState, type ReactNode } from "react";
import { v3api } from "../../api";
import StockAttribution from "../../StockAttribution";
import {
  defaultGraphicProps,
  GRAPHIC_REGISTRY,
  graphicGeometry,
  renderGraphic,
  type GraphicComponentId,
  type TrustedGraphicItem,
} from "../../../graphics/registry";
import { graphicPos, type CueKind } from "./insert";
import { assetTab, type LibraryAsset } from "./library";
import { DRAWER_MIME, encodePayload, type DrawerPayload } from "./wordDrop";

export const ICON: Record<CueKind, string> = { broll: "▣", graphic: "◆", music: "♪", sfx: "✧" };
export const TYPE_NAME: Record<CueKind, string> = { broll: "B-roll", graphic: "Graphic", music: "Music", sfx: "Sound" };

export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function templateItem(componentId: GraphicComponentId): TrustedGraphicItem {
  return {
    type: "graphic", id: `template-${componentId}`, componentId, componentVersion: 1,
    props: defaultGraphicProps(componentId), pos: graphicPos(componentId), scale: 1, tlStartF: 0, durF: 1,
  } as TrustedGraphicItem;
}

// A 16:9 window around a graphic with some frame showing, kept inside the canvas.
function zoomBox(box: { x: number; y: number; width: number; height: number }) {
  const width = Math.min(1920, Math.max(box.width * 1.6, box.height * 1.6 * (16 / 9)));
  const height = (width * 9) / 16;
  const x = Math.min(1920 - width, Math.max(0, box.x + box.width / 2 - width / 2));
  const y = Math.min(1080 - height, Math.max(0, box.y + box.height / 2 - height / 2));
  return `${x} ${y} ${width} ${height}`;
}

// The graphic at its real geometry in a `width`×`height` frame (1920×1080 by default), scaled into
// whatever box holds it: over a still of the video the whole frame shows where it sits; `zoom` frames
// just the graphic for a small tile.
export function GraphicFrame({ item, backdrop, className, zoom = false, width = 1920, height = 1080 }: {
  item: TrustedGraphicItem;
  backdrop: string | null;
  className: string;
  zoom?: boolean;
  width?: number;
  height?: number;
}) {
  const geometry = graphicGeometry(item, width, height);
  const { box } = geometry;
  return (
    <svg className={`gfx-frame ${className}`} viewBox={zoom ? zoomBox(box) : `0 0 ${width} ${height}`} aria-hidden="true">
      {backdrop && <image href={backdrop} width={width} height={height} preserveAspectRatio="xMidYMid slice" />}
      <foreignObject x={box.x} y={box.y} width={box.width} height={box.height}>{renderGraphic(item, geometry)}</foreignObject>
    </svg>
  );
}

function Tile({ kind, name, sub, payload, visual, draggable, onPick, onHover }: {
  kind: CueKind;
  name: string;
  sub: string;
  payload: DrawerPayload;
  visual: ReactNode;
  draggable: boolean;
  onPick: () => void;
  onHover?: (on: boolean) => void;
}) {
  return (
    <button
      type="button"
      className={`tile t-${kind}`}
      draggable={draggable}
      onDragStart={(event) => {
        event.dataTransfer.setData(DRAWER_MIME, encodePayload(payload));
        event.dataTransfer.setData("text/plain", name);
        event.dataTransfer.effectAllowed = "copy";
      }}
      onClick={onPick}
      onMouseEnter={onHover && (() => onHover(true))}
      onMouseLeave={onHover && (() => onHover(false))}
    >
      {visual ?? <span className="tile-ic" aria-hidden="true">{ICON[kind]}</span>}
      <span className="tile-name">{name}</span>
      <small>{sub}</small>
    </button>
  );
}

/** A B-roll picture: its thumbnail, or the video `clip` playing, muted and looping over the thumbnail as
 * its poster, while one is given. */
export function ClipThumb({ thumb, clip, fallback }: { thumb: string; clip: string | null; fallback: ReactNode }) {
  const [failed, setFailed] = useState(false);
  if (clip) return <video src={clip} poster={failed ? undefined : thumb} muted autoPlay loop playsInline draggable={false} />;
  if (failed) return fallback;
  return <img src={thumb} alt="" loading="lazy" draggable={false} onError={() => setFailed(true)} />;
}

export function AssetTile({ entry, draggable = true, onPick }: { entry: LibraryAsset; draggable?: boolean; onPick: () => void }) {
  const { asset, workspaceId, workspaceName } = entry;
  const [hover, setHover] = useState(false);
  const kind = assetTab(asset);
  const playable = kind === "broll" && asset.kind === "video";
  const sub = [asset.durationS ? clock(asset.durationS) : asset.kind === "image" ? "Photo" : null, workspaceName]
    .filter(Boolean).join(" · ");
  const icon = <span className="tile-ic" aria-hidden="true">{ICON.broll}</span>;
  return (
    <div className="asset-tile">
      <Tile
        kind={kind}
        name={asset.name}
        sub={sub}
        payload={{ type: "asset", workspaceId, assetId: asset.id }}
        visual={kind === "broll"
          ? <ClipThumb thumb={v3api.assetThumbUrl(workspaceId, asset.id)} clip={hover ? v3api.assetFileUrl(workspaceId, asset.id) : null} fallback={icon} />
          : null}
        draggable={draggable}
        onPick={onPick}
        onHover={playable ? setHover : undefined}
      />
      {asset.attribution && <StockAttribution attribution={asset.attribution} kind={asset.kind === "image" ? "photo" : "video"} />}
    </div>
  );
}

export function GraphicTile({ componentId, draggable = true, onPick }: {
  componentId: GraphicComponentId;
  draggable?: boolean;
  onPick: () => void;
}) {
  return (
    <Tile
      kind="graphic"
      name={GRAPHIC_REGISTRY[componentId].name}
      sub="Template"
      payload={{ type: "graphic", componentId }}
      visual={<GraphicFrame item={templateItem(componentId)} backdrop={null} className="tile-art" zoom />}
      draggable={draggable}
      onPick={onPick}
    />
  );
}
