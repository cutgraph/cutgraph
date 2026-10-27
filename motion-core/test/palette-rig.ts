import { characterOf } from '../src/index.ts';
import type { Character } from '../src/index.ts';
import { oval, PAPER, rigDraw } from '../src/rig.ts';
import type { Ramp } from '../src/rig.ts';

export const TEST_PALETTES: Record<string, Ramp> = {
  teal: ['#84ded3', '#2bb3a3', '#1b8176', '#12594f'],
  slate: ['#94a3b8', '#475569', '#334155', '#1e293b'],
  violet: ['#c4b5fd', '#8b5cf6', '#6d28d9', '#4c1d95'],
};

export function paletteRig(id: string, tall = false): Character {
  const glass: Ramp = ['#e0f4f7', '#adcfd9', '#7ba2b6', '#5a798e'];
  const draws = Object.fromEntries(Object.entries(TEST_PALETTES).map(([palette, ramp]) => [
    palette, rigDraw(ramp, (ctx, m) => {
      const dy = m.chest, head = dy + m.head;
      oval(ctx, 22, 37 + dy, 7, 5, glass, 3);
      oval(ctx, 29, 14 + head + m.lag, 4, 2, PAPER, 4);
      oval(ctx, 16, 11 + head + m.lag / 2, 5, 2.5 - m.lag / 2, ramp, 4);
      oval(ctx, 22, 24 + head, 14, 11, ramp, 5);
      oval(ctx, 33, 18 + head, 2, 1.5, PAPER, 3);
      oval(ctx, 21, 25 + head, 10, 9, glass);
      oval(ctx, 21, 25 + head, 8, 7, PAPER);
      return 23 + head;
    }),
  ]));
  return {
    ...characterOf(undefined), id, name: 'Synthetic rig', palettes: Object.keys(TEST_PALETTES),
    ...(tall ? { rows: 75, scale: 1.5, aspect: 0.5, crown: 69 } : {}),
    draw(ctx, cx, by, h, state, env) {
      ctx.save();
      ctx.translate(cx, by);
      ctx.scale(1, tall ? 1.5 : 1);
      (draws[state.palette ?? 'teal'] ?? draws.teal)(ctx, 0, 0, h / (tall ? 1.5 : 1), state, env);
      ctx.restore();
    },
  };
}
