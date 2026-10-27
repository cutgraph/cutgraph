import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BACKDROPS, CAMEO_IN, DIAGRAMS, LENS_EXPRESSIONS, LENS_MOVES, LENS_POSES, ROLE_LOOKS, drawSceneFrame, resolveShot, shotMode, shotTiming } from '../src/index.ts';
import type { ShotTiming } from '../src/index.ts';
import { nodeEnv } from '../scripts/node-env.ts';
import { ink, render } from './helpers.ts';

const ROLES = ['Hook', 'Problem', 'Approach', 'Build', 'Example', 'Check', 'Payoff', 'Wrap', 'Beat'];

test('every scene role has a valid default look', () => {
  assert.deepEqual(Object.keys(ROLE_LOOKS).sort(), [...ROLES].sort());
  for (const look of Object.values(ROLE_LOOKS)) {
    assert.ok(LENS_POSES.includes(look.pose) && LENS_MOVES.includes(look.move));
    assert.ok(look.expression in LENS_EXPRESSIONS && BACKDROPS.includes(look.backdrop) && DIAGRAMS.includes(look.diagram));
  }
});

test('mode defaults to footage and ignores unknown values', () => {
  assert.equal(shotMode(undefined), 'footage');
  assert.equal(shotMode({}), 'footage');
  assert.equal(shotMode({ mode: 'animation' }), 'animation');
  assert.equal(shotMode({ mode: 'cameo' }), 'cameo');
  assert.equal(shotMode({ mode: 'hologram' }), 'footage');
});

test('resolveShot fills missing or unknown ids from the role and the headline from the title', () => {
  assert.deepEqual(resolveShot(undefined, 'Payoff', 'We shipped it'), {
    character: 'lens', palette: 'periwinkle', ...ROLE_LOOKS.Payoff, headline: 'We shipped it', eyebrow: '', accent: '#f5c518',
  });
  const s = resolveShot({ pose: 'point', backdrop: 'nowhere', headline: '   ' }, 'Check', 'Review');
  assert.equal(s.pose, 'point');
  assert.equal(s.backdrop, ROLE_LOOKS.Check.backdrop);
  assert.equal(s.headline, 'Review');
  assert.equal(resolveShot({}, 'Mystery', 'x').pose, ROLE_LOOKS.Beat.pose);
});

test('resolveShot keeps a custom expression id for the host to resolve', () => {
  assert.equal(resolveShot({ expression: 'smug' }, 'Beat', 't').expression, 'smug');
  assert.equal(resolveShot({ expression: 'happy' }, 'Beat', 't').expression, 'happy');
});

test('shotTiming is scene-local seconds; the cue defaults to the first kept word, and only a named one sets inT', () => {
  assert.deepEqual(shotTiming(300, 315, 360, 30), { firstT: 0.5, cueT: 2, inT: 2 });
  assert.deepEqual(shotTiming(300, 315, null, 30), { firstT: 0.5, cueT: 0.5 });
  assert.deepEqual(shotTiming(300, null, null, 30), { firstT: 0.4, cueT: 0.4 });
  assert.deepEqual(shotTiming(300, 290, 280, 30), { firstT: 0, cueT: 0, inT: 0 });
});

test('drawSceneFrame: animation is opaque, cameo only touches the lower corner, footage draws nothing', () => {
  const env = nodeEnv();
  const shot = resolveShot({ mode: 'cameo' }, 'Wrap', 'Thanks');
  const timing = shotTiming(0, 12, null, 30);
  const [W, H] = [480, 270];
  const anim = render(W, H, (ctx) => drawSceneFrame(ctx, 'animation', shot, 1.2, timing, W, H, env));
  assert.equal(ink(anim), W * H);
  const cameo = render(W, H, (ctx) => drawSceneFrame(ctx, 'cameo', shot, 1.2, timing, W, H, env));
  const cameoInk = ink(cameo);
  assert.ok(cameoInk > 200 && cameoInk < W * H * 0.2);
  const { data } = cameo.ctx.getImageData(0, 0, W / 2, H);
  for (let i = 3; i < data.length; i += 4) assert.equal(data[i], 0, 'the left half stays transparent');
  assert.equal(ink(render(W, H, (ctx) => drawSceneFrame(ctx, 'footage', shot, 1.2, timing, W, H, env))), 0);
  const early = render(W, H, (ctx) => drawSceneFrame(ctx, 'cameo', shot, 0, timing, W, H, env));
  assert.ok(ink(early) < cameoInk, 'the cameo springs in from below the frame');
  const again = render(W, H, (ctx) => drawSceneFrame(ctx, 'cameo', shot, 1.2, timing, W, H, env));
  assert.equal(again.hash, cameo.hash, 'deterministic');
});

test('a cameo springs in on the cue word its shot names: nothing before it, then the uncued cameo delayed to it', () => {
  const env = nodeEnv();
  const [W, H] = [480, 270];
  const shot = resolveShot({ mode: 'cameo', pose: 'wave', expression: 'happy', move: 'wave' }, 'Beat', 'x');
  const cued = shotTiming(0, 12, 60, 30), plain = shotTiming(0, 12, null, 30);
  assert.deepEqual([cued.inT, plain.inT], [2, undefined]);
  const frame = (lt: number, timing: ShotTiming) => render(W, H, (ctx) => drawSceneFrame(ctx, 'cameo', shot, lt, timing, W, H, env));
  for (const lt of [0, 0.5, 1.2, 1.99]) assert.equal(ink(frame(lt, cued)), 0, `nothing before the cue word @${lt}`);
  for (const u of [0, 0.125, 0.25, 0.375, 1.5, 4]) assert.equal(frame(2 + u, cued).hash, frame(u, plain).hash, `${u}s after it springs in`);
  assert.ok(ink(frame(2 + CAMEO_IN, cued)) > 200, 'it lands, then holds through its move');
});
