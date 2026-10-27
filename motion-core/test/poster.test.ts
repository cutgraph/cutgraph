import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import {
  POSTER_CROP, POSTER_FORMATS, POSTER_WORDS, characterOf, drawCharacter, drawPoster, posterLabels, posterLayout, posterTitle, resolvePoster,
} from '../src/index.ts';
import type { Ctx, MotionEnv, Poster, PosterFormat, ProjectPoster } from '../src/index.ts';
import { posterPng } from '../bin/render-poster.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';

const FORMATS = Object.keys(POSTER_FORMATS) as PosterFormat[];

const GROUP_SAMPLE: ProjectPoster = {
  headline: 'Museum Visit',
  character: 'lens', pose: 'cheer', expression: 'excited',
  ensemble: [
    { character: 'lens', palette: 'periwinkle', pose: 'wave', expression: 'proud' },
    { character: 'lens', palette: 'periwinkle', pose: 'wave', expression: 'determined' },
    { character: 'lens', palette: 'periwinkle', pose: 'wave', expression: 'proud' },
  ],
};

function pixels(poster: Poster, format: PosterFormat, env: MotionEnv = nodeEnv(), scale = 1): { data: Uint8ClampedArray; hash: string; w: number; h: number } {
  const [W, H] = POSTER_FORMATS[format], w = Math.round(W * scale), h = Math.round(H * scale);
  const { ctx } = surface(w, h);
  ctx.scale(w / W, h / H);
  drawPoster(ctx, poster, W, H, env);
  const { data } = ctx.getImageData(0, 0, w, h);
  return { data, hash: createHash('sha256').update(data).digest('hex'), w, h };
}

test('resolvePoster fills a thumbnail with the poster looks: the lead resting, the ensemble pointing, on work', () => {
  const p = resolvePoster({ character: 'lens', ensemble: [{ character: 'lens', palette: 'periwinkle' }, { character: 'lens' }] });
  assert.deepEqual(p.lead, { character: 'lens', palette: 'periwinkle', pose: 'rest', expression: 'neutral' });
  assert.deepEqual(p.ensemble, [
    { character: 'lens', palette: 'periwinkle', pose: 'point', expression: 'neutral' },
    { character: 'lens', palette: 'periwinkle', pose: 'point', expression: 'neutral' },
  ]);
  assert.equal(p.backdrop, 'work');
  assert.deepEqual([p.headline, p.eyebrow, p.badges], ['', '', []]);
});

test('resolvePoster drops what a rig cannot draw, keeps a custom expression, and holds at most three members', () => {
  const custom = { eyes: 'wide', mouth: 'crooked' };
  const p = resolvePoster({
    character: 'nobody', palette: 'plaid', pose: 'not-a-pose', expression: 'smug', custom, backdrop: 'nowhere', accent: 'red',
    badges: [{ text: ' NEW ', tone: 'paper' }, { text: '' }], eyebrow: '  Episode 1 ',
    ensemble: [{ character: 'lens' }, { character: 'lens' }, { character: 'lens' }, { character: 'lens' }],
  });
  assert.deepEqual(p.lead, { character: 'nobody', palette: 'periwinkle', pose: 'rest', expression: 'smug', custom });
  assert.equal(p.ensemble.length, 3);
  assert.deepEqual([p.backdrop, p.accent, p.eyebrow], ['work', '#f5c518', 'Episode 1']);
  assert.deepEqual(p.badges, [{ text: 'NEW', tone: 'paper' }]);
});

test('a poster draws the same pixels every time, in any env, and carries nothing between draws', () => {
  const garden = resolvePoster(GROUP_SAMPLE), plain = resolvePoster({ headline: 'Just the default rig', backdrop: 'work' });
  for (const format of FORMATS) {
    const env = nodeEnv(), first = pixels(garden, format, env).hash;
    pixels(plain, format, env);
    pixels(garden, format === '16:9' ? '9:16' : '16:9', env);
    assert.equal(pixels(garden, format, env).hash, first, format);
    assert.equal(pixels(garden, format, nodeEnv()).hash, first, format);
  }
});

test('unknown poster badge tones draw like ink in preview and export', () => {
  const raw: ProjectPoster = {
    headline: 'Morning walk', accent: '#2bb3a3', badges: [{ text: 'Map', tone: 'neon' }],
    ensemble: [{ character: 'lens', badge: { text: 'Trail', tone: 'ultraviolet' } }],
  };
  const badges = [{ text: 'Map', tone: 'ink' }], label = { text: 'Trail', tone: 'ink' };
  const current: ProjectPoster = { ...raw, badges, ensemble: [{ character: 'lens', badge: label }] };
  assert.deepEqual(resolvePoster(raw), resolvePoster(current));
  assert.deepEqual(resolvePoster(raw).badges, badges);
  assert.deepEqual(resolvePoster(raw).ensemble.map((m) => m.badge), [label]);
  for (const format of FORMATS) {
    const [width, height] = POSTER_FORMATS[format];
    const { canvas, ctx } = surface(width, height);
    drawPoster(ctx, resolvePoster(raw), width, height, nodeEnv());
    assert.ok(canvas.toBuffer('image/png').equals(posterPng({ width, height, poster: current })), format);
    assert.ok(posterPng({ width, height, poster: raw }).equals(posterPng({ width, height, poster: current })), format);
  }
});

test('the Node render (render-poster.ts) is the preview drawing: the same PNG bytes for the same spec', () => {
  for (const format of FORMATS) {
    const [W, H] = POSTER_FORMATS[format];
    const { canvas, ctx } = surface(W, H);
    drawPoster(ctx, resolvePoster(GROUP_SAMPLE), W, H, nodeEnv());
    assert.ok(canvas.toBuffer('image/png').equals(posterPng({ width: W, height: H, poster: GROUP_SAMPLE })), format);
  }
});

test("a scaled preview canvas draws the same picture as the full-size render, shrunk", () => {
  const poster = resolvePoster(GROUP_SAMPLE);
  for (const format of FORMATS) {
    const small = pixels(poster, format, nodeEnv(), 0.25);
    const [W, H] = POSTER_FORMATS[format], full = surface(W, H), shrunk = surface(small.w, small.h);
    drawPoster(full.ctx, poster, W, H, nodeEnv());
    shrunk.ctx.drawImage(full.canvas as unknown as CanvasImageSource, 0, 0, small.w, small.h);
    const ref = shrunk.ctx.getImageData(0, 0, small.w, small.h).data;
    let diff = 0;
    for (let i = 0; i < ref.length; i++) if (i % 4 !== 3) diff += Math.abs(ref[i] - small.data[i]);
    assert.ok(diff / (small.w * small.h * 3) < 12, `${format}: mean channel difference ${(diff / (small.w * small.h * 3)).toFixed(1)}`);
  }
});

// Every performer's solid pixels, drawn alone at its slot on a canvas three frames wide, against the frame.
function extents(ctx: Ctx, draw: (ctx: Ctx) => void, W: number, H: number): { x0: number; y0: number; x1: number; y1: number } {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, 3 * W, 3 * H);
  ctx.translate(W, H);
  draw(ctx);
  const { data } = ctx.getImageData(0, 0, 3 * W, 3 * H);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let y = 0; y < 3 * H; y++) for (let x = 0; x < 3 * W; x++) {
    if (data[(y * 3 * W + x) * 4 + 3] < 128) continue;
    x0 = Math.min(x0, x - W); x1 = Math.max(x1, x + 1 - W); y0 = Math.min(y0, y - H); y1 = Math.max(y1, y + 1 - H);
  }
  return { x0, y0, x1, y1 };
}

test('every performer and the title stay inside the frame in both formats; only the lead is cropped, at the waist', () => {
  const env = nodeEnv();
  const casts: ProjectPoster[] = [
    GROUP_SAMPLE,
    { headline: 'One', character: 'lens', palette: 'periwinkle', ensemble: [{ character: 'lens' }] },
    { headline: 'How a longer headline fits into a small frame', eyebrow: 'Episode 12', badges: [{ text: 'NEW', tone: 'ink' }], character: 'lens' },
    { headline: 'Three wide guest', character: 'lens', ensemble: [{ character: 'lens' }, { character: 'lens' }, { character: 'lens' }] },
    { headline: 'Line up', character: 'lens', pose: 'wave', ensemble: [{ character: 'lens' }, { character: 'lens' }] },
  ];
  for (const format of FORMATS) {
    const [W, H] = POSTER_FORMATS[format], { ctx } = surface(3 * W, 3 * H);
    for (const stored of casts) {
      const poster = resolvePoster(stored), lead = characterOf(poster.lead.character);
      const L = posterLayout(W, H, lead, poster.ensemble.map((m) => characterOf(m.character)));
      const where = `${format} "${poster.headline}"`;
      const still = { move: 'idle', t: 0.6 };
      L.members.forEach((m, i) => {
        const e = extents(ctx, (c) => drawCharacter(c, m.x, m.by, m.h, { ...poster.ensemble[i], ...still }, env), W, H);
        assert.ok(e.x0 >= 0 && e.x1 <= W && e.y0 >= 0 && e.y1 <= H, `${where}: member ${i + 1} at ${JSON.stringify(e)}`);
      });
      const e = extents(ctx, (c) => {
        c.translate(2 * L.lead.x, 0);
        c.scale(-1, 1);
        drawCharacter(c, L.lead.x, L.lead.by, L.lead.h, { ...poster.lead, ...still }, env);
      }, W, H);
      assert.ok(e.x0 >= 0 && e.x1 <= W && e.y0 >= 0, `${where}: the lead at ${JSON.stringify(e)}`);
      assert.ok(L.lead.by - H <= POSTER_CROP * L.lead.h + 1, `${where}: the lead is cropped no higher than its waist`);
      assert.ok(L.lead.h > Math.max(0, ...L.members.map((m) => m.h)), `${where}: the lead is the biggest`);
      assert.equal(new Set(L.members.map((m) => m.g)).size <= 1, true, `${where}: the ensemble shares one pixel size`);

      const T = posterTitle(ctx, poster, L.title, env), [bx0, by0, bx1, by1] = T.bounds;
      assert.ok(bx0 >= 0 && by0 >= 0 && bx1 <= W && by1 <= H, `${where}: the title at ${T.bounds.map(Math.round)}`);
      assert.ok(T.lines.every((l) => l.split(' ').length <= Math.max(POSTER_WORDS, Math.ceil(poster.headline.split(/\s+/).length / L.title.lines))),
        `${where}: ${JSON.stringify(T.lines)}`);
      for (const m of L.members) {
        const head = m.by - m.h * 0.65;
        assert.ok(by1 <= head || bx1 <= m.x - m.w / 2 || bx0 >= m.x + m.w / 2, `${where}: the title covers a face (${Math.round(by1)} > ${Math.round(head)})`);
      }
    }
  }
});

test('a short title sets big, in at most three words a line, and a long one shrinks to fit', () => {
  const env = nodeEnv(), { ctx } = surface(10, 10);
  const [W, H] = POSTER_FORMATS['16:9'], character = characterOf('lens'), L = posterLayout(W, H, character, []);
  const short = posterTitle(ctx, resolvePoster({ headline: 'Museum Visit' }), L.title, env);
  const long = posterTitle(ctx, resolvePoster({ headline: 'A b c d e f g' }), L.title, env);
  assert.deepEqual(short.lines, ['MUSEUM', 'VISIT']);
  assert.ok(short.fs >= 0.12 * H, `${short.fs}px`);
  assert.deepEqual(long.lines, ['A B C', 'D E F', 'G']);
});

test('each performer wears a white outline, and the lead faces into the frame', () => {
  const [W, H] = POSTER_FORMATS['16:9'], poster = resolvePoster({ character: 'lens', backdrop: 'black', headline: '' });
  const { data, w } = pixels(poster, '16:9');
  const L = posterLayout(W, H, characterOf('lens'), []);
  // Walk in from the left edge along the lead's eye line: backdrop, then the white outline, then the ink.
  const y = Math.round(L.lead.by - L.lead.h * 0.55);
  let x = 0;
  while (x < w && data[(y * w + x) * 4] < 200) x++;
  const at = (i: number) => [...data.slice((y * w + i) * 4, (y * w + i) * 4 + 3)];
  assert.deepEqual(at(x), [255, 255, 255], 'white first');
  assert.ok(x > W / 2, 'the lead stands right of centre');
});

test('a member may carry a label: resolved like a title badge, drawn over its own head, inside the frame and clear of the title', () => {
  const labelled: ProjectPoster = {
    ...GROUP_SAMPLE,
    headline: 'Preparation',
    ensemble: [
      { ...GROUP_SAMPLE.ensemble![0], badge: { text: 'ONE', tone: 'accent' } },
      { ...GROUP_SAMPLE.ensemble![1], badge: { text: '   ', tone: 'ink' } },
      { ...GROUP_SAMPLE.ensemble![2], badge: { text: 'TWO', tone: 'accent' } },
    ],
  };
  const poster = resolvePoster(labelled);
  assert.deepEqual(poster.ensemble.map((m) => m.badge), [{ text: 'ONE', tone: 'accent' }, undefined, { text: 'TWO', tone: 'accent' }]);
  // An unknown tone falls back to ink, exactly as a title card's badge does (stagingOf).
  assert.deepEqual(resolvePoster({ ...GROUP_SAMPLE, ensemble: [{ ...GROUP_SAMPLE.ensemble![0], badge: { text: 'X', tone: 'neon' } }] }).ensemble[0].badge, { text: 'X', tone: 'ink' });
  const env = nodeEnv();
  for (const format of FORMATS) {
    const [W, H] = POSTER_FORMATS[format];
    const { ctx } = surface(W, H);
    const lead = characterOf(poster.lead.character), members = poster.ensemble.map((m) => characterOf(m.character));
    const L = posterLayout(W, H, lead, members);
    const labels = posterLabels(ctx as unknown as Ctx, poster, L, W, env);
    assert.deepEqual(labels.map((l) => [l.member, l.text]), [[0, 'ONE'], [2, 'TWO']], format);
    const [, , , titleBottom] = posterTitle(ctx as unknown as Ctx, poster, L.title, env).bounds;
    for (const l of labels) {
      const s = L.members[l.member];
      assert.ok(l.x - l.w / 2 >= 0 && l.x + l.w / 2 <= W, `${format} ${l.text} inside the frame`);
      assert.ok(l.y + l.h / 2 < s.by - s.h, `${format} ${l.text} above its member's head`);
      assert.ok(Math.abs(l.x - s.x) < s.w, `${format} ${l.text} over its own member`);
      assert.ok(l.y - l.h / 2 > titleBottom, `${format} ${l.text} below the title (${l.y - l.h / 2} > ${titleBottom})`);
    }
  }
  // Labels change the picture; an unlabelled poster draws exactly as before.
  assert.notEqual(pixels(poster, '16:9').hash, pixels(resolvePoster({ ...labelled, ensemble: GROUP_SAMPLE.ensemble }), '16:9').hash);
});

test('poster defaults preserve explicit flex and proud selections', () => {
  const poster = resolvePoster({ pose: 'flex', expression: 'proud', ensemble: [{ pose: 'wave', expression: 'happy' }] });
  assert.deepEqual([poster.lead.pose, poster.lead.expression], ['flex', 'proud']);
  assert.deepEqual([poster.ensemble[0].pose, poster.ensemble[0].expression], ['wave', 'happy']);
});
