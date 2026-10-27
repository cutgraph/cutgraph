// The tracking pan: the view pans so the whole world slides right across the frame, carrying the
// lead, who stands still in it, from just inside the left edge into its slot, easing out as it settles.
// The backdrop moves in parallax (its dot grid and motes slower than the stage, plus faint far shapes and
// floor markings that fade as the pan settles), so the move reads even where the lead travels little. A
// performer walking in from the right arrives as the pan settles. Once settled the frame is the stage
// exactly as laid out, so the next scene starts where this one ends. Pure functions of scene-local time.

import { widthOf } from './acting.ts';
import { clamp, ease, prog } from './draw.ts';
import type { Camera, Character, Ctx } from './types.ts';

/** Seconds the pan takes (or the whole scene, when shorter). */
export const PAN_S = 2.4;
// The shortest pan, of the frame's width, so a lead standing near the left edge still travels. The
// extras fade over the pan's last FADE, when the world has all but stopped.
const PAN_MIN = 0.1, FADE = 0.35;

/** The pan at a moment: the share of its distance still to travel, the extras' opacity and its length. */
export interface PanState {
  rest: number;
  alpha: number;
  dur: number;
}

/** The pan at scene-local time `lt`, or null for any other camera. */
export function panAt(camera: Camera | undefined, lt: number, endT?: number): PanState | null {
  if (camera !== 'pan') return null;
  const dur = endT !== undefined && endT > 0 ? Math.min(PAN_S, endT) : PAN_S, p = prog(lt, 0, dur);
  return { rest: 1 - ease.out(p), alpha: clamp((1 - p) / FADE), dur };
}

/**
 * How far the world slides in a W-wide frame: from where the lead (feet at `x`, `h` tall) stands just
 * inside the left edge to its slot, and at least PAN_MIN of the width.
 */
export function panDistance(x: number, h: number, rig: Character, W: number): number {
  return Math.max(x - (W * 0.03 + widthOf(rig, h) / 2), W * PAN_MIN);
}

/** Where the world is at a moment: whole screen pixels left of its settled place (0 once settled). */
export const panShift = (pan: PanState | null, distance: number): number => (pan ? -Math.round(distance * pan.rest) || 0 : 0);

// Parallax for the stage backdrops' own layers (scene.ts `backdrop`): the dot grid is a far wall, the
// motes float between it and the stage.
export const GRID_DEPTH = 0.5;
export const MOTE_DEPTH = 0.75;

// The extras: far shapes (a skyline of faint blocks, [width, height] of the frame's height) in slow
// parallax, then floor markings on the ground line and two rows in front of it, nearer and so faster:
// [depth, row (0 on the ground line, 1 at the frame's foot), spacing, dash length, dash height].
const FAR = 0.25;
const FLOOR: readonly (readonly [number, number, number, number, number])[] = [
  [1, 0, 0.09, 0.034, 0.006],
  [1.4, 0.38, 0.13, 0.05, 0.008],
  [1.9, 0.78, 0.18, 0.07, 0.011],
];
const BLOCKS: readonly (readonly [number, number])[] = [[0.1, 0.34], [0.16, 0.22], [0.08, 0.42], [0.2, 0.28], [0.12, 0.36], [0.14, 0.24]];

// x of a layer element that repeats every `period` pixels, scrolled by `shift`.
const wrapped = (x: number, shift: number, period: number): number => ((((x + shift) % period) + period) % period);

/**
 * The pan's extras over the backdrop, `shift` being the world's offset (panShift) and `ground` the
 * performers' feet line: faint far blocks in slow parallax and floor markings, all at `alpha`.
 */
export function panExtras(ctx: Ctx, W: number, H: number, ground: number, shift: number, alpha: number): void {
  if (alpha <= 0) return;
  const u = H / 1080;
  ctx.save();
  ctx.globalAlpha = alpha;
  const span = W + H * 0.4, rim = Math.max(2, Math.round(3 * u));
  BLOCKS.forEach(([w, h], i) => {
    const x = Math.round(wrapped((i * span) / BLOCKS.length, shift * FAR, span) - H * 0.2), y = Math.round(ground - h * H);
    ctx.fillStyle = 'rgba(255,255,255,.028)';
    ctx.fillRect(x, y, Math.round(w * H), Math.round(ground) - y);
    ctx.fillStyle = 'rgba(255,255,255,.05)';
    ctx.fillRect(x, y, Math.round(w * H), rim);
  });
  ctx.fillStyle = 'rgba(255,255,255,.07)';
  ctx.fillRect(0, Math.round(ground), W, Math.max(2, Math.round(3 * u)));
  for (const [depth, row, every, len, tall] of FLOOR) {
    const y = Math.round(ground + row * (H - ground) + 6 * u), period = Math.round(every * H * depth);
    ctx.fillStyle = `rgba(255,255,255,${(0.1 + 0.04 * depth).toFixed(3)})`;
    for (let x = wrapped(0, Math.round(shift * depth), period) - period; x < W; x += period) {
      ctx.fillRect(Math.round(x), y, Math.round(len * H * depth), Math.max(2, Math.round(tall * H)));
    }
  }
  ctx.restore();
}
