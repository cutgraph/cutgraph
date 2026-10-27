// Picker tile painters for the Shot view: pure functions of (canvas size, look, t) over motion-core.
import { drawCharacter, drawSceneFrame, type Ctx, type MotionEnv, type Performer, type Shot, type ShotTiming } from "motion-core";

const TILE_BG = "#161b21";
const LOOK_TIMING: ShotTiming = { firstT: 0.2, cueT: 0.6 };
/** A resting look tile shows the diagram after it has landed. */
export const LOOK_REST_T = 2.2;
export const CHARACTER_REST_T = 0.6;

/** A character, whole, standing in the tile. */
export function paintCharacterTile(ctx: Ctx, W: number, H: number, performer: Performer, env: MotionEnv): void {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = TILE_BG;
  ctx.fillRect(0, 0, W, H);
  drawCharacter(ctx, W / 2, H * 0.9, H * 0.82, performer, env);
}

/** A whole 16:9 shot (a duo when it has a co-star) scaled into the tile, for the backdrop and diagram pickers. */
export function paintLookTile(ctx: Ctx, W: number, H: number, shot: Shot, t: number, env: MotionEnv, timing = LOOK_TIMING): void {
  ctx.setTransform(W / 1920, 0, 0, H / 1080, 0, 0);
  drawSceneFrame(ctx, "animation", shot, t, timing, 1920, 1080, env);
}

const DUO_TIMING: ShotTiming = { firstT: 0.2, cueT: 1.2 };
const DUO_LOOP = 2.6;

/**
 * The moment an interaction tile shows: resting on the impact (or the pointer drawn, or the pair at
 * ease), and looping through the beat while hovered.
 */
export function interactionT(interaction: string, t: number | null): number {
  const cue = DUO_TIMING.cueT!;
  if (t !== null) return cue - 1 + (t % DUO_LOOP);
  return interaction === "fistbump" || interaction === "highfive" ? cue : interaction === "point" ? cue + 0.8 : LOOK_REST_T;
}

/** A duo shot playing one interaction, for the interaction picker. */
export function paintDuoTile(ctx: Ctx, W: number, H: number, shot: Shot, t: number | null, env: MotionEnv): void {
  paintLookTile(ctx, W, H, shot, interactionT(shot.interaction ?? "none", t), env, DUO_TIMING);
}
