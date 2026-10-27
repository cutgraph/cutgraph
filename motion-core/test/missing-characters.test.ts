import assert from 'node:assert/strict';
import { test } from 'node:test';
import { drawSceneFrame, FORMATS, resolveShot, type ProjectShot } from '../src/index.ts';
import { nodeEnv } from '../scripts/node-env.ts';
import { render } from './helpers.ts';

const cases: ProjectShot[] = [
  { mode: 'animation', character: 'retired-rig' },
  { mode: 'animation', costar: { character: 'retired-rig' }, interaction: 'face' },
  { mode: 'animation', ensemble: [{ character: 'retired-rig', move: 'enter' }] },
  { mode: 'cameo', character: 'retired-rig' },
  { mode: 'animation', layout: 'signoff', character: 'retired-rig' },
];

test('missing markers stay upright for leads, left-facing co-stars, entering ensembles and cameos', () => {
  for (const [W, H] of Object.values(FORMATS)) {
    for (const stored of cases) {
      for (const lt of [0.9, 2.4]) {
        const markers: { a: number; b: number; c: number; d: number }[] = [];
        const frame = render(W, H, (ctx) => {
          const fill = ctx.fillText.bind(ctx);
          ctx.fillText = (value, x, y) => {
            if (value === 'Missing character') markers.push(ctx.getTransform());
            fill(value, x, y);
          };
          drawSceneFrame(ctx, stored.mode ?? 'animation', resolveShot(stored, 'Beat', ''), lt,
            { firstT: 0, cueT: 0 }, W, H, nodeEnv());
        });
        assert.equal(markers.length, 1, JSON.stringify(stored));
        assert.ok(markers[0].a > 0 && markers[0].d > 0, 'text axes face right and down');
        assert.equal(markers[0].b, 0);
        assert.equal(markers[0].c, 0);
        assert.ok(frame.ctx.getImageData(0, 0, W, H).data.some((value) => value > 0));
      }
    }
  }
});
