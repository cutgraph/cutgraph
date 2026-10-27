// The shared cameo tile shows the scene character and animates while hovered.
// The drawer also lets the tile drag onto a word.
import { useState } from "react";
import { domEnv } from "../../../preview/SceneLayer";
import { DRAWER_MIME, encodePayload } from "../drawer/wordDrop";
import { useTileCanvas } from "../shot/Picker";
import { CHARACTER_REST_T, paintCharacterTile } from "../shot/paint";
import type { CameoPick } from "./cameo";

const ENV = domEnv();

export default function CameoTile({ cameo, draggable = false }: { cameo: CameoPick; draggable?: boolean }) {
  const [hover, setHover] = useState(false);
  const ref = useTileCanvas(
    (ctx, w, h, t) => paintCharacterTile(ctx, w, h, { ...cameo.performer, t: t ?? CHARACTER_REST_T }, ENV),
    JSON.stringify(cameo.performer),
    hover,
  );
  return (
    <button
      type="button"
      className="tile t-anim"
      draggable={draggable}
      onDragStart={(event) => {
        event.dataTransfer.setData(DRAWER_MIME, encodePayload({ type: "cameo" }));
        event.dataTransfer.setData("text/plain", `${cameo.name} cameo`);
        event.dataTransfer.effectAllowed = "copy";
      }}
      onClick={cameo.onPick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <canvas ref={ref} width={240} height={135} aria-hidden="true" />
      <span className="tile-name">{cameo.name} cameo</span>
      <small>Springs in on “{cameo.word}”</small>
    </button>
  );
}
