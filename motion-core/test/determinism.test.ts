import assert from 'node:assert/strict';
import { test } from 'node:test';
import { FORMATS, drawCaptions, drawGraphic, drawShot, timeline } from '../src/index.ts';
import type { Ctx, Format, MotionEnv } from '../src/index.ts';
import { sampleProject } from '../scripts/fixture.ts';
import { nodeEnv } from '../scripts/node-env.ts';
import { render } from './helpers.ts';

const project = sampleProject();
const TL = timeline(project);

function composite(format: Format, si: number, lt: number, env: MotionEnv): string {
  const [W, H] = FORMATS[format], S = TL.scenes[si], shot = project.scenes[si].shot!;
  return render(W, H, (ctx: Ctx) => {
    drawShot(ctx, shot, lt, S, W, H, env);
    drawGraphic(ctx, { item: 'lower-third', text: 'Your name · Studio', start: 0 }, 0.8, W, H, lt, env);
    drawGraphic(ctx, { item: 'character-cameo', start: 0 }, 1, W, H, lt, env);
    drawCaptions(ctx, S.words, S.start + lt, W, H, env);
  }).hash;
}

test('the same inputs render byte-identical frames', () => {
  const env = nodeEnv();
  for (const format of Object.keys(FORMATS) as Format[]) {
    for (const [si, lt] of [[0, 2.1], [3, 0.3], [5, 3.7]] as const) {
      assert.equal(composite(format, si, lt, env), composite(format, si, lt, env), format + ' scene ' + si);
    }
  }
});

test('frames carry no state between draws or envs', () => {
  const env = nodeEnv();
  const first = composite('16:9', 2, 1.9, env);
  composite('9:16', 5, 0.6, env);
  assert.equal(composite('16:9', 2, 1.9, env), first);
  assert.equal(composite('16:9', 2, 1.9, nodeEnv()), first);
});
