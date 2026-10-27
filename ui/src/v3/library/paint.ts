// Library painters over motion-core: pure functions of (canvas size, performer).
import { PACK_RIGS, characterOf, drawCharacter, type HeadCrop, type Ctx, type MotionEnv, type Performer } from "motion-core";
import { paintCharacterTile } from "../story/shot/paint";

const TILE_BG = "#161b21";

// Head crops for the expression composer.
const HEADS: Readonly<Record<string, HeadCrop>> = {
  lens: { rows: 50, foot: 50, span: 30, cols: 34, centre: 23 },
};

export const headCrop = (id: string): HeadCrop | undefined =>
  (Object.prototype.hasOwnProperty.call(PACK_RIGS, id) ? PACK_RIGS[id].head : undefined)
  ?? (Object.prototype.hasOwnProperty.call(HEADS, id) ? HEADS[id] : undefined);

/**
 * A close-up of the character's head, where every expression part shows, at the largest whole-pixel
 * scale; the whole character for a rig without a head crop.
 */
export function paintHeadTile(ctx: Ctx, W: number, H: number, performer: Performer, env: MotionEnv): void {
  const crop = headCrop(characterOf(performer.character).id);
  if (!crop) return paintCharacterTile(ctx, W, H, performer, env);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = TILE_BG;
  ctx.fillRect(0, 0, W, H);
  const g = Math.max(1, Math.floor(Math.min(W / crop.cols, H / crop.span)));
  drawCharacter(ctx, W / 2, H / 2 + (crop.foot - crop.centre) * g, crop.rows * g, performer, env);
}
