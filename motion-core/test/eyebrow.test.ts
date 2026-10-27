import { characterOf as defaultRigOf, DEFAULT_CHARACTER as defaultRigId } from '../src/index.ts';
const DEFAULT_RIG = defaultRigOf(defaultRigId);
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ACCENT, EYEBROW, FORMATS, drawSceneFrame, duoLayout, headlineRuns, layout, resolveShot } from '../src/index.ts';
import type { Ctx, Format, ProjectShot } from '../src/index.ts';
import { sceneFrames } from '../bin/render-scene.ts';
import { fontPrint, nodeEnv } from '../scripts/node-env.ts';
import { render } from './helpers.ts';
import { HEADLINE_FRAMES, headlineFrameHash } from './headline-frames.ts';
import { loadGolden } from './goldens.ts';

const env = nodeEnv();
const CUSTOM = { eyebrow: 'CUSTOM GARDEN', accent: '#2bb3a3' };

const golden = loadGolden<{ fonts: string; frames: Record<string, string> }>('headline-frames');

test('the default eyebrow matches its recording, with or without the host naming it', { skip: golden.skip }, () => {
  const GOLDEN = golden.value!;
  assert.equal(fontPrint(), GOLDEN.fonts, 'Registered fonts differ from the golden; regenerate intentionally');
  assert.equal(HEADLINE_FRAMES.length, Object.keys(GOLDEN.frames).length);
  for (const f of HEADLINE_FRAMES) {
    assert.equal(headlineFrameHash(f), GOLDEN.frames[f.name], f.name);
    assert.equal(headlineFrameHash(f, undefined, { eyebrow: EYEBROW, accent: ACCENT }), GOLDEN.frames[f.name], `${f.name}, named`);
  }
  assert.notEqual(headlineFrameHash(HEADLINE_FRAMES[1], undefined, CUSTOM), GOLDEN.frames[HEADLINE_FRAMES[1].name], 'a brand changes the frame');
});

test('resolveShot takes the eyebrow and its accent from the host, else config defaults; empty hides it', () => {
  assert.deepEqual([EYEBROW, ACCENT], ['', '#f5c518']);
  const pick = (s: ProjectShot) => { const r = resolveShot(s, 'Build', 'Launch'); return [r.eyebrow, r.accent]; };
  assert.deepEqual(pick({}), [EYEBROW, ACCENT]);
  assert.deepEqual(pick(CUSTOM), ['CUSTOM GARDEN', '#2bb3a3']);
  assert.deepEqual(pick({ eyebrow: '', accent: '#2BB3A3' }), ['', '#2BB3A3']);
  for (const accent of ['red', '#abcde', '#2bb3a3aa', 'url(x)', '']) assert.deepEqual(pick({ eyebrow: 'X', accent }), ['X', ACCENT], accent);
  assert.deepEqual(pick({ eyebrow: 42 as unknown as string }), [EYEBROW, ACCENT]);
  assert.deepEqual(pick({ costar: { character: 'lens' }, ...CUSTOM }), ['CUSTOM GARDEN', '#2bb3a3'], 'a duo too');
});

test('headlineRuns sets the eyebrow in its accent; without one the headline lines keep their place', () => {
  render(1920, 1080, (ctx) => {
    const L = layout('16:9');
    const ours = headlineRuns(ctx, 'Prepare the sauce', L, env), custom = headlineRuns(ctx, 'Prepare the sauce', L, env, CUSTOM.eyebrow, CUSTOM.accent);
    const none = headlineRuns(ctx, 'Prepare the sauce', L, env, '');
    assert.deepEqual([custom[0].text, custom[0].color], ['CUSTOM GARDEN', '#2bb3a3']);
    assert.deepEqual(none, ours);
    assert.deepEqual(custom.slice(1), ours, 'hiding the eyebrow preserves headline geometry');
    const centred = headlineRuns(ctx, 'Prepare the sauce', { ...L, center: true }, env, CUSTOM.eyebrow);
    ctx.font = `800 ${centred[0].size}px ${env.fontFamily}`;
    assert.ok(Math.abs(centred[0].x + ctx.measureText(CUSTOM.eyebrow).width / 2 - (L.tx + L.tw / 2)) < 1e-6, 'centred in a duo');
  });
});

test('a 40-character brand stays inside the 5% title-safe margin in every format, one character or two', () => {
  const brand = 'FLOWERS BLOOM UNDER A CLEAR EVENING SKY!';
  assert.equal(brand.length, 40);
  for (const format of Object.keys(FORMATS) as Format[]) {
    const [W, H] = FORMATS[format];
    render(W, H, (ctx) => {
      for (const L of [layout(format), duoLayout(W, H, DEFAULT_RIG, DEFAULT_RIG, true)]) {
        const [r] = headlineRuns(ctx, 'Launch', L, env, brand);
        ctx.font = `${r.weight} ${r.size}px ${env.fontFamily}`;
        const m = ctx.measureText(r.text);
        assert.ok(r.x - m.actualBoundingBoxLeft >= W * 0.05 && r.x + m.actualBoundingBoxRight <= W * 0.95, `${format}${L.center ? ' duo' : ''}`);
        assert.ok(r.y - m.actualBoundingBoxAscent >= H * 0.05, `${format} top`);
      }
    });
  }
});

// Where the eyebrow's glyphs sit in a 16:9 frame, padded a little.
const GOLD_EYEBROW = 'FIELD NOTES';
function eyebrowBox(ctx: Ctx, stored: ProjectShot, W: number, H: number) {
  const s = resolveShot(stored, 'Build', 'Explore the garden');
  const L = stored.costar ? duoLayout(W, H, DEFAULT_RIG, DEFAULT_RIG, true) : layout('16:9');
  // A centred shorter eyebrow starts further right; include both sets of glyph bounds.
  const boxes = [GOLD_EYEBROW, stored.eyebrow ?? GOLD_EYEBROW].map((text) => {
    const [r] = headlineRuns(ctx, s.headline, L, env, text);
    ctx.font = `${r.weight} ${r.size}px ${env.fontFamily}`;
    const m = ctx.measureText(text);
    return { x0: r.x - m.actualBoundingBoxLeft, x1: r.x + m.actualBoundingBoxRight,
      y0: r.y - m.actualBoundingBoxAscent, y1: r.y + m.actualBoundingBoxDescent };
  });
  return { x0: Math.floor(Math.min(...boxes.map((b) => b.x0))) - 4, x1: Math.ceil(Math.max(...boxes.map((b) => b.x1))) + 4,
    y0: Math.floor(Math.min(...boxes.map((b) => b.y0))) - 4, y1: Math.ceil(Math.max(...boxes.map((b) => b.y1))) + 4 };
}

function frame(stored: ProjectShot) {
  const [W, H] = [1920, 1080];
  return render(W, H, (ctx) => drawSceneFrame(ctx, 'animation', resolveShot(stored, 'Build', 'Explore the garden'), 2.5, { firstT: 0.4, cueT: 1 }, W, H, env));
}

const exactly = (data: Uint8ClampedArray, i: number, hex: string): boolean =>
  data[i] === parseInt(hex.slice(1, 3), 16) && data[i + 1] === parseInt(hex.slice(3, 5), 16) && data[i + 2] === parseInt(hex.slice(5, 7), 16);

test('a custom eyebrow renders in its accent in the eyebrow\'s place, and hiding it leaves the backdrop there', () => {
  for (const base of [{ mode: 'animation', pose: 'point' }, { mode: 'animation', costar: { character: 'lens' }, interaction: 'face' }] as ProjectShot[]) {
    const ours = frame({ ...base, eyebrow: GOLD_EYEBROW }), custom = frame({ ...base, ...CUSTOM }), hidden = frame({ ...base, ...CUSTOM, eyebrow: '' });
    const box = eyebrowBox(ours.ctx, { ...base, ...CUSTOM }, 1920, 1080);
    const [a, b, c] = [ours, custom, hidden].map((f) => f.ctx.getImageData(0, 0, 1920, 1080).data);
    let outside = 0, teal = 0, gold = 0, hiddenInk = 0;
    for (let y = 0; y < 1080; y++) for (let x = 0; x < 1920; x++) {
      const i = (y * 1920 + x) * 4, inBox = x >= box.x0 && x < box.x1 && y >= box.y0 && y < box.y1;
      const same = (p: Uint8ClampedArray) => p[i] === a[i] && p[i + 1] === a[i + 1] && p[i + 2] === a[i + 2];
      if (!inBox && (!same(b) || !same(c))) outside++;
      if (!inBox) continue;
      if (exactly(b, i, CUSTOM.accent)) teal++;
      if (exactly(a, i, ACCENT)) gold++;
      if (exactly(c, i, CUSTOM.accent) || exactly(c, i, ACCENT)) hiddenInk++;
    }
    const what = base.costar ? 'duo' : 'single';
    assert.equal(outside, 0, `${what}: only the eyebrow changes`);
    assert.ok(gold > 150 && teal > 150, `${what}: ours is gold (${gold} px), the custom teal (${teal} px)`);
    assert.equal(hiddenInk, 0, `${what}: no eyebrow when hidden`);
  }
});

test('the Node renderer draws the eyebrow its render request carries', () => {
  const spec = {
    width: 1920, height: 1080, fps: 30, durF: 16, mode: 'animation', role: 'Hook', title: 'Launch',
    shot: { mode: 'animation', ...CUSTOM }, startF: 0, firstWordF: 0, cueWordF: null, alpha: false,
  };
  let last: Buffer | undefined;
  for (const f of sceneFrames(spec)) last = f;
  const data = new Uint8ClampedArray(last!.buffer, last!.byteOffset, last!.byteLength);
  const f = render(1920, 1080, () => {});
  const box = eyebrowBox(f.ctx, spec.shot, 1920, 1080);
  let teal = 0;
  for (let y = box.y0; y < box.y1; y++) for (let x = box.x0; x < box.x1; x++) if (exactly(data, (y * 1920 + x) * 4, CUSTOM.accent)) teal++;
  assert.ok(teal > 150, `the exported frame's eyebrow is custom teal (${teal} px)`);
});
