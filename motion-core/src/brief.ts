// The classic `brief` diagram (scene.ts): a light summary card with an accent header
// and three labelled rows for a goal, audience and context.

import { rr, text } from './draw.ts';
import { ACCENT } from './scene.ts';
import type { Ctx } from './types.ts';

/** The card's width and height over its layout's diagram box width (scene.ts diagramRow). */
export const BRIEF_WIDE = 2.3, BRIEF_TALL = 1.25;

/** The card `w` x `h` at (x, y), lettered at `s` (its box width over 200), grown to `p` about its centre. */
export function drawBrief(ctx: Ctx, x: number, y: number, w: number, h: number, s: number, p: number, font: string): void {
  ctx.save();
  ctx.translate(x + w / 2, y + h / 2);
  ctx.scale(p, p);
  ctx.translate(-w / 2, -h / 2);
  rr(ctx, 0, 0, w, h, 18);
  ctx.fillStyle = '#eef2f0';
  ctx.fill();
  ctx.fillStyle = ACCENT;
  rr(ctx, 0, 0, w, h * 0.2, [18, 18, 0, 0]);
  ctx.fill();
  text(ctx, 'BRIEF', w * 0.06, h * 0.14, Math.round(26 * s), '#111214', font, 800);
  ['Goal', 'Audience', 'Context'].forEach((l, i) => {
    const y = h * (0.36 + i * 0.2);
    text(ctx, l, w * 0.06, y + 10 * s, Math.round(26 * s), '#2a3036', font, 700);
    ctx.fillStyle = '#c7d0d4';
    ctx.fillRect(w * 0.36, y, w * (0.5 - i * 0.08), 12 * s);
  });
  ctx.restore();
}
