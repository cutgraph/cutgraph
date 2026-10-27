import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import {
  FORMATS, beginCamera, drawCharacter, drawSceneFrame, endCamera, resolveShot,
} from '../src/index.ts';
import type { ProjectShot } from '../src/index.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';

const env = nodeEnv();
const member = { pose: 'rest', expression: 'neutral', move: 'idle' };
const layouts: Array<[string, ProjectShot]> = [
  ['stage', {}], ['duo', { costar: member }],
  ['four performers', { costar: member, ensemble: [member, member] }],
  ['five performers', { costar: member, ensemble: [member, member, member] }],
  ['signoff', { layout: 'signoff', mark: 'TEXT' }],
];

for (const camera of [undefined, 'push-in', 'pull-out'] as const) {
  for (const [host, matrix] of [
    ['scaled', [0.5, 0, 0, 0.5, 0, 0]],
    ['affine', [0.45, 0.05, -0.025, 0.4, 30, 25]],
  ] as const) {
    test(`${camera ?? 'still'}: 1080p and 4K projects match on the same ${host} preview canvas`, () => {
      const shot = resolveShot({ mode: 'animation', ...member, camera,
        hideHeadline: true, diagram: 'none', backdrop: 'black' }, '', '');
      const render = (scale: number) => {
        const { ctx } = surface(960, 540);
        const [a, b, c, d, e, f] = matrix;
        ctx.setTransform(a / scale, b / scale, c / scale, d / scale, e, f);
        const before = ctx.getTransform();
        drawSceneFrame(ctx, 'animation', shot, camera === 'pull-out' ? 0.4 : 1.95,
          { endT: 2 }, 1920 * scale, 1080 * scale, env);
        assert.deepEqual(ctx.getTransform(), before, 'the host transform is restored');
        return ctx.getImageData(0, 0, 960, 540).data;
      };
      const small = render(1), big = render(2);
      let differing = 0;
      for (let i = 0; i < small.length; i += 4) {
        if (small.slice(i, i + 4).some((v, c) => v !== big[i + c])) differing++;
      }
      assert.equal(differing, 0, 'project resolution must not change preview pixels');
    });
  }
}

for (const [format, [W, H]] of Object.entries(FORMATS)) for (const [name, fields] of layouts) {
  test(`${format} ${name}: renderer zoom endpoints are distinct, bounded centre crops`, () => {
    const matrices = [];
    for (const [camera, lt, zoom] of [['push-in', 3, 1.18], ['pull-out', 0, 1.12]] as const) {
      const { ctx } = surface(8, 8);
      const shot = resolveShot({ mode: 'animation', ...fields, camera }, '', '');
      const move = beginCamera(ctx, shot, lt, { endT: 3 }, W, H), m = ctx.getTransform();
      assert.ok(move, 'each endpoint visibly zooms even at coarse rig sizes');
      assert.ok(Math.abs(m.a - zoom) < 1e-6, `actual scale ${m.a} reaches ${zoom}`);
      assert.ok(Math.abs(m.e - W / 2 * (1 - zoom)) < 0.001);
      assert.ok(Math.abs(m.f - H / 2 * (1 - zoom)) < 0.001);
      matrices.push([m.a, m.d, m.e, m.f]);
      endCamera(ctx, move);
      assert.deepEqual([ctx.getTransform().a, ctx.getTransform().d, ctx.getTransform().e, ctx.getTransform().f], [1, 1, 0, 0]);
    }
    assert.notDeepEqual(matrices[0], matrices[1]);
    for (const camera of ['push-in', 'pull-out'] as const) {
      const scales = Array.from({ length: 17 }, (_, i) => {
        const { ctx } = surface(8, 8), lt = camera === 'push-in' ? 2.2 + i * 0.05 : i * 0.05;
        const move = beginCamera(ctx, resolveShot({ mode: 'animation', ...fields, camera }, '', ''), lt, { endT: 3 }, W, H);
        const k = ctx.getTransform().a;
        endCamera(ctx, move);
        return k;
      });
      for (let i = 1; i < scales.length; i++) {
        const step = scales[i] - scales[i - 1];
        assert.ok(camera === 'push-in' ? step > 0 : step < 0, 'the actual framing advances smoothly without rig-cell plateaus');
        assert.ok(Math.abs(step) < 0.025, 'adjacent camera samples stay bounded');
      }
    }
  });

  test(`${format} ${name}: both cameras change actual scene pixels and short scenes remain safe`, () => {
    const render = (camera: 'push-in' | 'pull-out' | undefined, lt: number, endT: number) => {
      const { ctx } = surface(W / 4, H / 4);
      ctx.scale(0.25, 0.25);
      const before = ctx.getTransform();
      drawSceneFrame(ctx, 'animation', resolveShot({ mode: 'animation', ...member, ...fields,
        camera, hideHeadline: true, diagram: 'none', backdrop: 'black' }, '', ''), lt, { endT }, W, H, env);
      assert.deepEqual(ctx.getTransform(), before);
      const data = ctx.getImageData(0, 0, W / 4, H / 4).data;
      assert.ok(data.every((v, i) => i % 4 !== 3 || v === 255), 'opaque backdrop covers the full frame');
      return createHash('sha256').update(data).digest('hex');
    };
    for (const duration of [1 / 60, 0.2, 3]) {
      assert.notEqual(render('push-in', duration, duration), render('pull-out', 0, duration), 'independent endpoint frames remain distinct');
      assert.notEqual(render('push-in', duration, duration), render(undefined, duration, duration));
      assert.notEqual(render('pull-out', 0, duration), render(undefined, 0, duration));
      assert.equal(render('push-in', 0, duration), render(undefined, 0, duration));
      assert.equal(render('pull-out', duration, duration), render(undefined, duration, duration));
    }
    for (const camera of ['push-in', 'pull-out'] as const)
      assert.equal(render(camera, 0, 0), render(undefined, 0, 0));
  });
}

test('continuous camera framing keeps sprite colours crisp and places the rig grid and feet through the same transform', () => {
  for (const [W, H] of Object.values(FORMATS)) for (const camera of ['push-in', 'pull-out'] as const) {
    const x = W / 2, by = H * 0.75, h = 250;
    const sprite = (zoomed: boolean) => {
      const { ctx } = surface(W, H);
      const move = zoomed ? beginCamera(ctx, resolveShot({ mode: 'animation', camera }, '', ''), camera === 'push-in' ? 3 : 0, { endT: 3 }, W, H) : null;
      drawCharacter(ctx, x, by, h, { pose: 'rest', expression: 'neutral', t: 0 }, env);
      endCamera(ctx, move);
      return ctx.getImageData(0, 0, W, H).data;
    };
    const still = sprite(false), cropped = sprite(true), k = camera === 'push-in' ? 1.18 : 1.12;
    const colours = new Set<string>();
    for (let i = 0; i < still.length; i += 4) if (still[i + 3] === 255) colours.add(`${still[i]},${still[i + 1]},${still[i + 2]}`);
    let checked = 0, foot = 0;
    for (let y = 0; y < H; y++) for (let px = 0; px < W; px++) {
      const i = (y * W + px) * 4;
      if (cropped[i + 3] !== 255) continue;
      assert.ok(colours.has(`${cropped[i]},${cropped[i + 1]},${cropped[i + 2]}`), 'nearest-neighbour framing invents no blended sprite colours');
      const sx = Math.floor((px + 0.5 - W / 2 * (1 - k)) / k), sy = Math.floor((y + 0.5 - H / 2 * (1 - k)) / k);
      const j = (sy * W + sx) * 4;
      assert.deepEqual(cropped.slice(i, i + 4), still.slice(j, j + 4), 'rig cells follow the centre crop at device-pixel sampling');
      checked++;
      foot = Math.max(foot, y);
    }
    assert.ok(checked > 1000);
    assert.ok(foot < by * k + H / 2 * (1 - k) && foot > (by - 15) * k + H / 2 * (1 - k), 'feet stay just above their transformed ground anchor');
  }
});
