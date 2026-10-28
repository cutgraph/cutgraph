import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  DEFAULT_CHARACTER, POSTER_FORMATS, characterOf, drawPoster, drawSceneFrame, duoLayout, ensembleLayout, FORMATS, resolvePoster, resolveShot,
  type Ctx, type MotionEnv, type PosterFormat, type ProjectShot,
} from '../src/index.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';
import { render } from './helpers.ts';

/** Records where each "Missing character" marker's text lands in frame pixels, and its size. */
function markers(ctx: Ctx, into: { x: number; y: number; size: number }[]): void {
  const fill = ctx.fillText.bind(ctx);
  ctx.fillText = (value, x, y) => {
    if (value === 'Missing character') {
      const m = ctx.getTransform();
      into.push({ x: m.a * x + m.c * y + m.e, y: m.b * x + m.d * y + m.f, size: m.a * Number(/([\d.]+)px/.exec(ctx.font)![1]) });
    }
    fill(value, x, y);
  };
}

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

// The stand-in rig's palette: only its body draws these colours on a black stage.
const STAND_IN = new Set(['208,221,252', '159,186,233', '113,140,191', '80,103,142']);

test('the marker follows the stand-in\'s body through its own entrance, alone, in a duo and in an ensemble', () => {
  const lead: ProjectShot = { mode: 'animation', character: 'retired-rig', move: 'enter', pose: 'rest', hideHeadline: true, diagram: 'none', backdrop: 'black' };
  const other = { character: 'lens', move: 'idle' };
  const rig = characterOf(DEFAULT_CHARACTER);
  const cases: { name: string; stored: ProjectShot; edge: (W: number, H: number) => number }[] = [
    { name: 'alone', stored: lead, edge: (W) => W },
    { name: 'duo', stored: { ...lead, costar: other, interaction: 'face' },
      edge: (W, H) => { const D = duoLayout(W, H, rig, rig, true); return (D.lead.x + D.costar.x) / 2; } },
    { name: 'ensemble', stored: { ...lead, ensemble: [other, other] },
      edge: (W, H) => { const [a, b] = ensembleLayout(W, H, [rig, rig, rig]).slots; return (a.x + b.x) / 2; } },
  ];
  for (const [format, [W, H]] of Object.entries(FORMATS)) for (const { name, stored, edge } of cases) {
    const right = edge(W, H);
    const at = (lt: number) => {
      const found: { x: number; y: number; size: number }[] = [];
      const { ctx } = surface(W, H);
      markers(ctx, found);
      drawSceneFrame(ctx, 'animation', resolveShot(stored, 'Beat', ''), lt, { firstT: 0, cueT: 0 }, W, H, nodeEnv());
      const data = ctx.getImageData(0, 0, W, H).data;
      let x0 = Infinity, x1 = -Infinity;
      for (let y = 0; y < H; y++) for (let x = 0; x < right; x++) {
        const i = (y * W + x) * 4;
        if (STAND_IN.has(`${data[i]},${data[i + 1]},${data[i + 2]}`)) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); }
      }
      assert.equal(found.length, 1, `${format} ${name} @${lt}`);
      return { marker: found[0].x, x0, x1 };
    };
    // Settled on its spot, the whole body is on stage; on its way in it may still be partly off the left edge.
    const rest = at(2), width = rest.x1 - rest.x0;
    assert.ok(rest.x0 > 0 && rest.x1 < right && Math.abs(rest.marker - (rest.x0 + rest.x1) / 2) < width * 0.15, `${format} ${name}: settled`);
    for (const lt of [0.3, 0.4, 0.5, 0.6]) {
      const { marker, x1 } = at(lt), body = x1 - width / 2;
      assert.ok(x1 < rest.x1 - width * 0.1, `${format} ${name} @${lt}: still walking in`);
      assert.ok(Math.abs(marker - body) < width * 0.15, `${format} ${name} @${lt}: the marker at ${marker.toFixed(0)} sits under the body at ${body.toFixed(0)}`);
    }
  }
});

test('thumbnail markers stay inside the frame: the lead\'s, whose feet are cropped, and every member\'s', () => {
  const poster = resolvePoster({ headline: 'Missing', character: 'retired-rig', ensemble: [{ character: 'retired-rig' }, { character: 'lens' }, { character: 'retired-rig' }] });
  for (const format of Object.keys(POSTER_FORMATS) as PosterFormat[]) {
    const [W, H] = POSTER_FORMATS[format], found: { x: number; y: number; size: number }[] = [], base = nodeEnv();
    const env: MotionEnv = { ...base, createCanvas: (w, h) => {
      const canvas = base.createCanvas(w, h), oc = canvas.getContext('2d');
      if (oc) markers(oc, found);
      return canvas;
    } };
    const { ctx } = surface(W, H);
    drawPoster(ctx, poster, W, H, env);
    assert.equal(found.length, 3, `${format}: the lead's marker and the two missing members'`);
    for (const m of found) {
      const box = { x0: m.x - m.size * 4.5, x1: m.x + m.size * 4.5, y0: m.y - m.size * 1.1, y1: m.y + m.size * 0.4 };
      assert.ok(box.x0 >= 0 && box.x1 <= W && box.y0 >= 0 && box.y1 <= H, `${format}: a marker at ${JSON.stringify(box)} is inside ${W}x${H}`);
    }
  }
});
