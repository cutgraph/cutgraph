import { LENS_EYES, LENS_EXPRESSIONS, LENS_MOUTHS, LENS_MOVES, LENS_POSES, oval, PAPER, rigDraw } from './rig.ts';
import type { Ramp } from './rig.ts';
import type { Character } from './types.ts';

const PAL: Ramp = ['#d0ddfc', '#9fbae9', '#718cbf', '#50678e'];
const GLASS: Ramp = ['#e0f4f7', '#adcfd9', '#7ba2b6', '#5a798e'];

export const LENS: Character = {
  id: 'lens',
  name: 'Lens',
  owner: 'brand',
  palettes: ['periwinkle'],
  parts: { eyes: LENS_EYES, mouth: LENS_MOUTHS },
  expressions: LENS_EXPRESSIONS,
  poses: LENS_POSES,
  moves: LENS_MOVES,
  rows: 50, scale: 1, aspect: 0.7, crown: 46,
  hands: { point: [19.5, 16.5], wave: [17.5, 26.5] },
  draw: rigDraw(PAL, (ctx, m) => {
    const dy = m.chest, head = dy + m.head;
    oval(ctx, 22, 37 + dy, 7, 5, GLASS, 3);
    oval(ctx, 29, 14 + head + m.lag, 4, 2, PAPER, 4);
    oval(ctx, 16, 11 + head + m.lag / 2, 5, 2.5 - m.lag / 2, PAL, 4);
    oval(ctx, 22, 24 + head, 14, 11, PAL, 5);
    oval(ctx, 33, 18 + head, 2, 1.5, PAPER, 3);
    oval(ctx, 21, 25 + head, 10, 9, GLASS);
    oval(ctx, 21, 25 + head, 8, 7, PAPER);
    return 23 + head;
  }),
};
