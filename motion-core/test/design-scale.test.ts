// A frame larger than its 1080-class design (a 4K export) draws the same design scaled up: averaged
// back down, it is the 1080 frame, glows and shadows included.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { FORMATS, designScale, drawSceneFrame, resolveShot, shotTiming } from '../src/index.ts';
import type { Format, ProjectShot, ShotMode } from '../src/index.ts';
import { atScale, shadow } from '../src/draw.ts';
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

test('the design scale is 1 at every 1080-class size and below, and 2 at 4K', () => {
  for (const [W, H] of Object.values(FORMATS)) assert.equal(designScale(W, H), 1);
  assert.equal(designScale(320, 180), 1);
  assert.equal(designScale(3840, 2160), 2);
  assert.equal(designScale(2160, 3840), 2);
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
