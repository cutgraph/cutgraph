import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import {
  FORMATS, beginCamera, drawCharacter, drawSceneFrame, endCamera, resolveShot,
} from '../src/index.ts';
import { atScale } from '../src/draw.ts';
import type { MotionEnv, ProjectShot, ShotTiming } from '../src/index.ts';
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
      // A sample landing exactly on a cell edge may resolve to either side; Skia's Linux and
      // Windows builds round it differently, so only that tie accepts the neighbouring cell.
      const cells = (p: number) => {
        const s = Math.floor(p);
        return Math.abs(p - Math.round(p)) < 1e-6 ? [s, Math.round(p) - 1] : [s];
      };
      const fx = (px + 0.5 - W / 2 * (1 - k)) / k, fy = (y + 0.5 - H / 2 * (1 - k)) / k;
      const actual = cropped.slice(i, i + 4).join();
      const sources = cells(fy).flatMap((sy) => cells(fx).map((sx) => still.slice((sy * W + sx) * 4, (sy * W + sx) * 4 + 4).join()));
      assert.ok(sources.includes(actual), `rig cells follow the centre crop at device-pixel sampling (${px},${y})`);
      checked++;
      foot = Math.max(foot, y);
    }
    assert.ok(checked > 1000);
    assert.ok(foot < by * k + H / 2 * (1 - k) && foot > (by - 15) * k + H / 2 * (1 - k), 'feet stay just above their transformed ground anchor');
  }
});

// A picker tile draws a whole 16:9 shot scaled into the tile, at rest or looping while hovered, with no scene length.
function tile(fields: ProjectShot, t: number, timing: ShotTiming = { firstT: 0.2, cueT: 0.6 }): string {
  const { ctx } = surface(192, 108);
  ctx.setTransform(192 / 1920, 0, 0, 108 / 1080, 0, 0);
  drawSceneFrame(ctx, 'animation', resolveShot({ mode: 'animation', ...member, ...fields }, 'Beat', 'Tile'), t, timing, 1920, 1080, env);
  return createHash('sha256').update(ctx.getImageData(0, 0, 192, 108).data).digest('hex');
}

test('a push-in picker tile without a scene length shows the full frame, at rest and through its hover loop', () => {
  for (const fields of [{ diagram: 'blank' }, { diagram: 'grid', costar: member }, { costar: member, ensemble: [member] }] as ProjectShot[]) {
    for (const t of [0, 0.4, 1.2, 2.2, 3.1]) {
      assert.equal(tile({ ...fields, camera: 'push-in' }, t), tile(fields, t), `${JSON.stringify(fields)} @${t}: no crop`);
    }
    assert.equal(tile({ ...fields, camera: 'pull-out' }, 2.2), tile(fields, 2.2), 'a pull-out tile at rest has returned to the full frame');
    assert.notEqual(tile({ ...fields, camera: 'push-in' }, 2.2, { firstT: 0.2, cueT: 0.6, endT: 2.2 }), tile(fields, 2.2),
      'given its length, the same shot pushes in by its end');
  }
});

test('a 4K export samples zoomed rigs exactly: every rig pixel is its 1080p pixel enlarged 2x', () => {
  const [W, H] = FORMATS['16:9'];
  for (const [camera, lt] of [['push-in', 2.45], ['push-in', 3], ['pull-out', 0], ['pull-out', 0.3]] as const) {
    const rigs = (k: number) => {
      const { ctx } = surface(W * k, H * k);
      atScale(ctx, k, () => {
        const move = beginCamera(ctx, resolveShot({ mode: 'animation', camera }, '', ''), lt, { endT: 3 }, W, H);
        drawCharacter(ctx, W * 0.3, H * 0.8, 430, { pose: 'wave', expression: 'happy', t: 0.7 }, env);
        drawCharacter(ctx, W * 0.62, H * 0.8, 260, { pose: 'point', expression: 'curious', t: 1.3 }, env);
        endCamera(ctx, move);
      });
      return ctx.getImageData(0, 0, W * k, H * k).data;
    };
    const hd = rigs(1), uhd = rigs(2);
    let opaque = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      if (hd[i + 3] !== 255) continue;
      opaque++;
      for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
        const j = ((2 * y + dy) * 2 * W + 2 * x + dx) * 4;
        assert.deepEqual(uhd.slice(j, j + 4), hd.slice(i, i + 4), `${camera} @${lt}: rig pixel (${x}, ${y}) enlarges exactly`);
      }
    }
    let uhdOpaque = 0;
    for (let i = 3; i < uhd.length; i += 4) if (uhd[i] === 255) uhdOpaque++;
    assert.ok(opaque > 20000, `${camera} @${lt}: both rigs are drawn`);
    assert.equal(uhdOpaque, opaque * 4, `${camera} @${lt}: no rig pixel exists at 4K alone`);
  }
});

test('a 4K multi-performer camera scene reuses one pixel layer, and shared layers never carry pixels between performers or frames', () => {
  let made = 0;
  const counting = (): MotionEnv => {
    const base = nodeEnv();
    return { ...base, createCanvas: (w, h) => { made++; return base.createCanvas(w, h); } };
  };
  const shots: ProjectShot[] = [
    { camera: 'push-in', costar: member, ensemble: [member, { ...member, move: 'wave' }, member] },
    { camera: 'push-in' },
    { camera: 'pull-out', costar: { ...member, move: 'hop' }, interaction: 'face' },
  ];
  const frame = (fields: ProjectShot, lt: number, frameEnv: MotionEnv) => {
    const { ctx } = surface(960, 540);
    ctx.scale(0.25, 0.25);
    drawSceneFrame(ctx, 'animation', resolveShot({ mode: 'animation', ...member, ...fields, hideHeadline: true, diagram: 'none', backdrop: 'black' }, '', ''),
      lt, { endT: 3 }, 3840, 2160, frameEnv);
    return createHash('sha256').update(ctx.getImageData(0, 0, 960, 540).data).digest('hex');
  };
  const scene = counting(), times = Array.from({ length: 24 }, (_, f) => 2.2 + f / 30);
  for (const lt of times) frame(shots[0], lt, scene);
  // The rig canvas, then the layer at most twice as the zoom grows: not once per performer per frame.
  assert.ok(made <= 3, `${made} canvases for 24 frames of five performers`);

  const cases = shots.flatMap((fields) => [0, 0.3, 2.5, 3].map((lt) => ({ fields, lt })));
  const fresh = cases.map(({ fields, lt }) => frame(fields, lt, nodeEnv()));
  const shared = nodeEnv(), order = [...cases.keys()].reverse();
  for (const i of [...order, ...cases.keys()]) {
    assert.equal(frame(cases[i].fields, cases[i].lt, shared), fresh[i], `${JSON.stringify(cases[i].fields)} @${cases[i].lt}: no layer state`);
  }
});
