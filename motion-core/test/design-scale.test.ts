// Every frame draws the 1080-class design at its own size. A 4K export, averaged back down, is the 1080
// frame, glows and shadows included; a 720p video's preview sets its text where the 1080 export does.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { FORMATS, designScale, drawSceneFrame, resolveShot, shotTiming } from '../src/index.ts';
import type { Format, ProjectShot, ShotMode } from '../src/index.ts';
import type { Ctx } from '../src/index.ts';
import { atScale, shadow } from '../src/draw.ts';
import { sceneFrames } from '../bin/render-scene.ts';
import type { SceneSpec } from '../bin/render-scene.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';

const env = nodeEnv();
const timing = shotTiming(0, 12, 30, 30, 150);

/** Premultiplied RGBA of one frame, so an average over a transparent edge weighs colour by coverage. */
function frame(mode: ShotMode, shot: ProjectShot, lt: number, W: number, H: number): Float32Array {
  const { ctx } = surface(W, H);
  drawSceneFrame(ctx, mode, resolveShot({ mode, ...shot }, 'Beat', 'A scene title'), lt, timing, W, H, env);
  const { data } = ctx.getImageData(0, 0, W, H);
  const out = new Float32Array(data.length);
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3] / 255;
    out[i] = data[i] * a; out[i + 1] = data[i + 1] * a; out[i + 2] = data[i + 2] * a; out[i + 3] = data[i + 3];
  }
  return out;
}

/** The mean of every n x n block of a W x H frame. */
function blocks(f: Float32Array, W: number, H: number, n: number): Float32Array {
  const bw = Math.floor(W / n), bh = Math.floor(H / n), out = new Float32Array(bw * bh * 4);
  for (let y = 0; y < bh * n; y++) for (let x = 0; x < bw * n; x++) {
    const i = (y * W + x) * 4, o = (Math.floor(y / n) * bw + Math.floor(x / n)) * 4;
    for (let c = 0; c < 4; c++) out[o + c] += f[i + c] / (n * n);
  }
  return out;
}

function psnr(a: Float32Array, b: Float32Array): number {
  let se = 0;
  for (let i = 0; i < a.length; i++) se += (a[i] - b[i]) ** 2;
  return 10 * Math.log10((255 * 255) / (se / a.length));
}

const CASES: Array<[string, ShotMode, ProjectShot, Format]> = [
  ['a headline over glowing boxes', 'animation', { pose: 'cheer', headline: 'Real export in 4K', diagram: 'glow' }, '16:9'],
  ['the route with its glowing dot', 'animation', { pose: 'point', headline: 'One route', diagram: 'route' }, '16:9'],
  ['an independent list', 'animation', { pose: 'rest', expression: 'neutral', headline: 'Items', diagram: 'list' }, '16:9'],
  ['a phone screen and its drop shadow', 'animation', { layout: 'screen', headline: 'On your phone' }, '16:9'],
  ['a pushed-in camera', 'animation', { pose: 'wave', headline: 'Closer', camera: 'push-in' }, '16:9'],
  ['a vertical headline', 'animation', { pose: 'cheer', headline: 'Shorts, sharper', diagram: 'checks' }, '9:16'],
  ['a square headline', 'animation', { pose: 'point', headline: 'Square', diagram: 'scatter' }, '1:1'],
  ['a cameo over footage', 'cameo', { bubble: 'say' }, '16:9'],
];
for (const format of Object.keys(FORMATS) as Format[]) for (const camera of ['push-in', 'pull-out'] as const) {
  const member = { pose: 'rest', expression: 'neutral', move: 'idle' };
  CASES.push([`five performers with ${camera} in ${format}`, 'animation', {
    ...member, camera, hideHeadline: true, diagram: 'none', backdrop: 'black',
    costar: member, ensemble: [member, member, member],
  }, format]);
}

test('the design scale is 1 at every 1080-class size, 2/3 at 720p and 2 at 4K', () => {
  for (const [W, H] of Object.values(FORMATS)) assert.equal(designScale(W, H), 1);
  assert.equal(designScale(1280, 720), 720 / 1080);
  assert.equal(designScale(720, 1280), 720 / 1080);
  assert.equal(designScale(3840, 2160), 2);
  assert.equal(designScale(2160, 3840), 2);
});

interface Run { text: string; x: number; y: number; size: number }

/** Every string drawn on a W x H surface while `draw` runs, placed and sized as fractions of the frame. */
function textRuns(W: number, H: number, draw: () => void): Run[] {
  const proto = Object.getPrototypeOf(surface(2, 2).ctx) as Ctx;
  const fill = proto.fillText;
  const runs: Run[] = [];
  proto.fillText = function (this: Ctx, s: string, x: number, y: number, maxWidth?: number) {
    if (this.canvas.width === W && this.canvas.height === H) {
      const m = this.getTransform(), px = Number(/([\d.]+)px/.exec(this.font)?.[1]);
      runs.push({ text: s, x: (m.a * x + m.c * y + m.e) / W, y: (m.b * x + m.d * y + m.f) / H, size: (px * Math.hypot(m.a, m.b)) / H });
    }
    return maxWidth === undefined ? fill.call(this, s, x, y) : fill.call(this, s, x, y, maxWidth);
  };
  try {
    draw();
  } finally {
    proto.fillText = fill;
  }
  return runs;
}

const SCENE: Omit<SceneSpec, 'width' | 'height' | 'shot'> = {
  fps: 30, durF: 45, mode: 'animation', role: 'Hook', title: 'Intro', startF: 300, firstWordF: 309, cueWordF: 318, alpha: false,
};
const TEXT_CASES: Array<[string, ProjectShot, Format]> = [
  ['a wrapping headline with an eyebrow', { pose: 'point', eyebrow: 'Step one', headline: 'A headline long enough to wrap onto more lines', diagram: 'list' }, '16:9'],
  ['a vertical headline', { pose: 'cheer', headline: 'Shorts keep the same words per line', diagram: 'checks' }, '9:16'],
  ['a title card', { layout: 'title', headline: 'One title, every size', eyebrow: 'Chapter' }, '16:9'],
];

// The preview paints a video's own canvas (ui/src/preview/SceneLayer.tsx paintScene: drawSceneFrame at the
// composition size), while the export renders the scene at the chosen format (scene_render.render_request).
test('a 720p video previews its text where and as large as its 1080 export draws it', () => {
  for (const [name, shot, format] of TEXT_CASES) {
    const [W, H] = FORMATS[format], [w, h] = [(W * 2) / 3, (H * 2) / 3];
    const spec: SceneSpec = { ...SCENE, width: W, height: H, shot: { mode: 'animation', ...shot } };
    const frames = sceneFrames(spec);
    for (let k = 0; k < spec.durF - 1; k++) frames.next();
    const exported = textRuns(W, H, () => frames.next());
    const lt = (spec.durF - 1) / spec.fps, timing = shotTiming(spec.startF, spec.firstWordF, spec.cueWordF, spec.fps, spec.durF);
    const preview = textRuns(w, h, () => {
      const { ctx } = surface(w, h);
      drawSceneFrame(ctx, 'animation', resolveShot(spec.shot ?? {}, spec.role, spec.title), lt, timing, w, h, env);
    });
    assert.ok(preview.length > 1, `${name}: the preview draws its text`);
    assert.deepEqual(preview.map((r) => r.text), exported.map((r) => r.text), `${name}: the same lines`);
    preview.forEach((r, i) => {
      const e = exported[i];
      for (const key of ['x', 'y', 'size'] as const) {
        assert.ok(Math.abs(r[key] - e[key]) < 1e-6, `${name}, "${r.text}" ${key}: preview ${r[key]} vs export ${e[key]}`);
      }
    });
  }
});

// Pixel by pixel, the 4K frame's finer edges (text, and the sprites' nearest-neighbour steps, which land on
// half pixels instead of whole ones) differ a little; over 8 x 8 blocks, what's left is anything broad,
// like a glow drawn at the wrong radius.
test('a 4K frame, averaged down, is its 1080-class frame', () => {
  for (const [name, mode, shot, format] of CASES) {
    const [W, H] = FORMATS[format];
    const times = name.startsWith('five performers') ? shot.camera === 'pull-out' ? [0, 0.4] : [4.6, 5] : [1.2, 4.9];
    for (const lt of times) {
      const big = frame(mode, shot, lt, 2 * W, 2 * H), small = frame(mode, shot, lt, W, H);
      const fine = psnr(blocks(big, 2 * W, 2 * H, 2), small);
      const broad = psnr(blocks(big, 2 * W, 2 * H, 16), blocks(small, W, H, 8));
      assert.ok(fine > 36 && broad > 46, `${name} at ${lt}s: ${fine.toFixed(1)} dB per pixel, ${broad.toFixed(1)} dB per block`);
    }
  }
});

test('a shadow is set in design pixels: at twice the size it blurs and drops twice as far', () => {
  const { ctx } = surface(4, 4);
  shadow(ctx, '#000', 10, 3);
  shadow(ctx, '#000', 10);
  assert.deepEqual([ctx.shadowBlur, ctx.shadowOffsetY], [10, 3], 'the offset is left alone when none is given');
  atScale(ctx, 2, () => {
    shadow(ctx, '#000', 10, 3);
    assert.deepEqual([ctx.shadowBlur, ctx.shadowOffsetY], [20, 6]);
  });
});

test('every canvas shadow goes through shadow(), since the transform never scales one', () => {
  const src = join(import.meta.dirname, '../src');
  for (const file of readdirSync(src).filter((f) => f.endsWith('.ts') && f !== 'draw.ts')) {
    const sets = [...readFileSync(join(src, file), 'utf8').matchAll(/\.shadow(?:Blur|OffsetX|OffsetY)\s*=\s*([^;\n]+)/g)];
    const direct = sets.filter((m) => m[1].trim() !== '0').map((m) => m[0]);
    assert.deepEqual(direct, [], `${file} sets a shadow directly`);
  }
});
