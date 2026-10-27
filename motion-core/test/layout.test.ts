import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DIAGRAMS, FORMATS, GRAPHICS, drawCaptions, drawGraphic, drawShot, headlineRuns, layout, timeline } from '../src/index.ts';
import type { Format } from '../src/index.ts';
import { sampleProject } from '../scripts/fixture.ts';
import { nodeEnv } from '../scripts/node-env.ts';
import { ink, render } from './helpers.ts';

const env = nodeEnv();
const project = sampleProject();
const TL = timeline(project);
const formats = Object.keys(FORMATS) as Format[];
const headlines = [...project.scenes.map((sc) => sc.shot!.headline), 'Pause at the decisions that matter, then move forward.'];

test('every format lays out every sample scene, diagram and graphic', () => {
  assert.deepEqual(formats, ['16:9', '9:16', '1:1']);
  for (const format of formats) {
    const [W, H] = FORMATS[format], L = layout(format);
    assert.deepEqual([L.W, L.H], [W, H]);
    project.scenes.forEach((sc, si) => {
      const S = TL.scenes[si];
      for (const lt of [0, 0.5, (S.cueT ?? 1) + 1.4]) {
        const f = render(W, H, (ctx) => {
          drawShot(ctx, sc.shot!, lt, S, W, H, env);
          drawCaptions(ctx, S.words, S.start + lt, W, H, env);
        });
        assert.ok(ink(f) === W * H, format + ' scene ' + si + ' left gaps');
      }
    });
    for (const diagram of DIAGRAMS) {
      render(W, H, (ctx) => drawShot(ctx, { ...project.scenes[0].shot!, diagram }, 2.5, { cueT: 1, firstT: 0.4 }, W, H, env));
    }
    for (const item of GRAPHICS) {
      const f = render(W, H, (ctx) => drawGraphic(ctx, { item, text: 'One · Two · Three', start: 0 }, 1, W, H, 1.6, env));
      assert.ok(ink(f) > 0, format + ' ' + item + ' rendered blank');
    }
  }
});

test('the headline stays inside a 5% title-safe margin in every format', () => {
  for (const format of formats) {
    const [W, H] = FORMATS[format], L = layout(format);
    render(W, H, (ctx) => {
      for (const s of headlines) {
        for (const r of headlineRuns(ctx, s, L, env)) {
          ctx.font = r.weight + ' ' + r.size + 'px ' + env.fontFamily;
          const m = ctx.measureText(r.text), where = format + ' "' + r.text + '"';
          assert.ok(r.x - m.actualBoundingBoxLeft >= W * 0.05, where + ' crosses the left margin');
          assert.ok(r.x + m.actualBoundingBoxRight <= W * 0.95, where + ' crosses the right margin');
          assert.ok(r.y - m.actualBoundingBoxAscent >= H * 0.05, where + ' crosses the top margin');
          assert.ok(r.y + m.actualBoundingBoxDescent <= H * 0.95, where + ' crosses the bottom margin');
        }
      }
    });
  }
});
