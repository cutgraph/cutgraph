import { createHash } from 'node:crypto';
import type { Ctx } from '../src/index.ts';
import { surface } from '../scripts/node-env.ts';

export interface Frame {
  hash: string;
  ctx: Ctx;
  w: number;
  h: number;
}

export function render(w: number, h: number, paint: (ctx: Ctx) => void): Frame {
  const { canvas, ctx } = surface(w, h);
  paint(ctx);
  return { hash: createHash('sha256').update(canvas.toBuffer('image/png')).digest('hex'), ctx, w, h };
}

/** Number of pixels with any opacity: a blank (fully transparent) frame has none. */
export function ink(f: Frame): number {
  const { data } = f.ctx.getImageData(0, 0, f.w, f.h);
  let n = 0;
  for (let i = 3; i < data.length; i += 4) if (data[i] > 0) n++;
  return n;
}
