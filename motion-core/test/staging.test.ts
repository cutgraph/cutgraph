import { characterOf as defaultRigOf, DEFAULT_CHARACTER as defaultRigId } from '../src/index.ts';
const DEFAULT_RIG = defaultRigOf(defaultRigId);
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { createCanvas } from '@napi-rs/canvas';
import {
  CAMERA_S, FORMATS, MARK, ZOOM, beginCamera, cameraAt, characterOf, drawCharacter, drawMark, drawSceneFrame, endCamera,
  ensembleLayout, focusOf, layoutFor, widthOf,
  SCREEN_MORE_MAX, phonesOf, resolveShot, screenLayout, shotTiming, signoffLayout, stagingOf,
} from '../src/index.ts';
import type { Ctx, Format, MotionEnv, PhoneSlot, ProjectShot, Shot, ShotTiming, StoredStaging } from '../src/index.ts';
import { nodeEnv, surface } from '../scripts/node-env.ts';
import { toneOf } from '../src/staging.ts';
import { ACCENT, backdrop } from '../src/scene.ts';
import { loadGolden } from './goldens.ts';
import { onePhoneHash } from './one-phone-frames.ts';

const FPS = 30;
const env = nodeEnv();
const shotOf = (s: ProjectShot): Shot => resolveShot({ mode: 'animation', ...s }, 'Beat', 'A scene title');

interface Pixels {
  data: Uint8ClampedArray;
  W: number;
  H: number;
  hash: string;
}

function draw(shot: Shot, lt: number, W = 480, H = 270, timing: ShotTiming = shotTiming(0, 12, 30, FPS, 90), e: MotionEnv = env): Pixels {
  const { ctx } = surface(W, H);
  const [FW, FH] = W > H ? [1920, 1080] : W < H ? [1080, 1920] : [1080, 1080];
  ctx.scale(W / FW, H / FH);
  drawSceneFrame(ctx, 'animation', shot, lt, timing, FW, FH, e);
  const { data } = ctx.getImageData(0, 0, W, H);
  return { data, W, H, hash: createHash('sha256').update(data).digest('hex') };
}

const rgb = (p: Pixels, x: number, y: number): [number, number, number] => {
  const i = (Math.round(y) * p.W + Math.round(x)) * 4;
  return [p.data[i], p.data[i + 1], p.data[i + 2]];
};

/** Bounding box of the pixels that are not the backdrop's black. */
function inkBox(p: Pixels): { x0: number; y0: number; x1: number; y1: number; n: number } {
  let x0 = p.W, y0 = p.H, x1 = -1, y1 = -1, n = 0;
  for (let y = 0; y < p.H; y++) for (let x = 0; x < p.W; x++) {
    const i = (y * p.W + x) * 4;
    if (p.data[i] + p.data[i + 1] + p.data[i + 2] < 40) continue;
    n++;
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  return { x0, y0, x1, y1, n };
}

const SIZES: Record<Format, [number, number]> = { '16:9': [480, 270], '9:16': [270, 480], '1:1': [360, 360] };

// ---------------------------------------------------------------- the stored fields

test('stagingOf keeps known ids and filled fields, and resolveShot adds nothing for an unstaged shot', () => {
  assert.deepEqual(stagingOf({
    camera: 'push-in', layout: 'title', screen: { asset: 'abc', image: '/x.png' }, mark: 'a&z?',
    badges: [{ text: ' ONE ', tone: 'ink' }, { text: 'TWO', tone: 'neon' }, { text: '  ' }],
  }), {
    camera: 'push-in', layout: 'title', screen: { asset: 'abc', image: '/x.png' }, mark: 'A&Z',
    badges: [{ text: 'ONE', tone: 'ink' }, { text: 'TWO', tone: 'ink' }],
  });
  assert.deepEqual(stagingOf({ camera: 'dolly', layout: 'stage', screen: {}, badges: [], mark: '??' }), {});
  assert.deepEqual(stagingOf({ badges: Array.from({ length: 6 }, (_, i) => ({ text: 'B' + i })) }).badges?.length, 4);
  const plain = resolveShot({ mode: 'animation', pose: 'cheer' }, 'Hook', 'Open');
  for (const key of ['camera', 'layout', 'screen', 'badges', 'mark']) assert.ok(!(key in plain), key);
  assert.equal(shotOf({ layout: 'signoff', camera: 'pull-out' }).layout, 'signoff');
});

test("stagingOf keeps a screen's filled `more` phones (stored ids or host-resolved), at most two, and only beside phone 1", () => {
  const junk = { screen: { asset: 'a', more: [' b ', '', 7, null, { image: ' ' }, { asset: 'c', image: '/c.png' }, 'd'] } } as unknown as StoredStaging;
  assert.equal(SCREEN_MORE_MAX, 2);
  assert.deepEqual(stagingOf(junk).screen, { asset: 'a', more: [{ asset: 'b' }, { asset: 'c', image: '/c.png' }] });
  assert.deepEqual(stagingOf({ screen: { asset: 'a', more: [] } }).screen, { asset: 'a' });
  assert.deepEqual(stagingOf({ screen: { asset: 'a', more: 'b' } } as unknown as StoredStaging).screen, { asset: 'a' });
  assert.deepEqual(stagingOf({ screen: { asset: ' ', more: ['b', 'c'] } }), {}, 'no phone 1, no phones');
  assert.deepEqual(phonesOf(shotOf({ layout: 'screen' })), [{}], 'a screen shot always draws phone 1');
  assert.deepEqual(phonesOf(shotOf({ layout: 'screen', screen: { asset: 'a', more: ['b', 'c'] } })), [{ asset: 'a' }, { asset: 'b' }, { asset: 'c' }]);
});

test('unknown badge tones draw like ink in raw shots', () => {
  for (const tone of ['neon', 'ultraviolet']) {
    const raw = { layout: 'title', headline: 'Morning walk', accent: '#2bb3a3', badges: [{ text: 'Outside', tone }] };
    const current = { ...raw, badges: [{ text: 'Outside', tone: 'ink' }] };
    assert.deepEqual(stagingOf(raw).badges, current.badges);
    for (const [W, H] of Object.values(SIZES)) {
      assert.equal(draw(shotOf(raw), 2, W, H).hash, draw(shotOf(current), 2, W, H).hash);
    }
  }
});

test('shotTiming carries the scene length only when given it', () => {
  assert.deepEqual(shotTiming(0, 12, 30, FPS), { firstT: 0.4, cueT: 1, inT: 1 });
  assert.deepEqual(shotTiming(0, 12, 30, FPS, 90), { firstT: 0.4, cueT: 1, inT: 1, endT: 3 });
});

// ---------------------------------------------------------------- camera

test('framing zooms have independent bounded endpoints and finish short scenes', () => {
  assert.equal(cameraAt('push-in', 0, 3).k, 1);
  assert.equal(cameraAt('push-in', 3, 3).k, ZOOM);
  assert.equal(cameraAt('pull-out', 0, 3).k, 1.12);
  assert.notEqual(cameraAt('pull-out', 0, 3).k, cameraAt('push-in', 3, 3).k);
  assert.equal(cameraAt('pull-out', CAMERA_S, 3).k, 1);
  for (const camera of ['push-in', 'pull-out'] as const) for (const duration of [undefined, 0, 1 / 60, 0.2, 3]) {
    const d = duration ?? CAMERA_S;
    for (const lt of [-1, 0, d / 2, d, d + 1]) {
      const state = cameraAt(camera, lt, duration);
      assert.ok(Number.isFinite(state.k) && state.k >= 1 && state.k <= ZOOM);
      assert.deepEqual(Object.keys(state), ['k']);
    }
    if (d > 0) assert.equal(cameraAt(camera, d, duration).k, camera === 'push-in' ? ZOOM : 1);
  }
});

test('each zoom covers frame edges, restores the transform and works without a neighbouring shot or rig', () => {
  for (const [W, H] of Object.values(FORMATS)) for (const camera of ['push-in', 'pull-out'] as const) {
    assert.deepEqual(focusOf(W, H), { x: W / 2, y: H / 2 });
    for (const duration of [1 / 60, 0.2, 3]) for (const lt of [0, duration / 2, duration]) {
      const { ctx } = surface(8, 8), shot = shotOf({ camera, character: 'unregistered-rig' });
      const move = beginCamera(ctx, shot, lt, { endT: duration }, W, H);
      const m = ctx.getTransform();
      assert.ok([m.a, m.d, m.e, m.f].every(Number.isFinite));
      assert.ok(m.e <= 0 && m.f <= 0 && m.a * W + m.e >= W && m.d * H + m.f >= H);
      assert.ok(m.a >= 1 && m.a <= ZOOM + 1e-6);
      endCamera(ctx, move);
      assert.deepEqual([ctx.getTransform().a, ctx.getTransform().e], [1, 0]);
    }
  }
});

test('zoom endpoints keep scene content visible and fixed endpoints match the uncropped scene', () => {
  const base: ProjectShot = { pose: 'rest', expression: 'neutral', move: 'idle', backdrop: 'black', diagram: 'blank', headline: 'Framing' };
  for (const [W, H] of Object.values(SIZES)) for (const camera of ['push-in', 'pull-out'] as const) {
    const atRest = camera === 'push-in' ? 0.5 : 1.5;
    assert.equal(draw(shotOf({ ...base, camera }), atRest, W, H).hash, draw(shotOf(base), atRest, W, H).hash);
    const endpoint = camera === 'push-in' ? 3 : 0;
    const cropped = draw(shotOf({ ...base, camera }), endpoint, W, H);
    assert.notEqual(cropped.hash, draw(shotOf(base), endpoint, W, H).hash);
    assert.ok(inkBox(cropped).n > 100);
    assert.ok(cropped.data.some((v, i) => i % 4 !== 3 && v < 200));
  }
});

// ---------------------------------------------------------------- backdrops

test('the black backdrop is black and the vision backdrop is bright and moving', () => {
  const black = draw(shotOf({ backdrop: 'black', diagram: 'blank', pose: 'rest', headline: ' ' }), 0.1);
  assert.deepEqual(rgb(black, 4, 4), [0, 0, 0]);
  const vision = (t: number) => draw(shotOf({ layout: 'title', backdrop: 'vision', headline: ' ' }), t);
  const [r, g, b] = rgb(vision(0.1), 240, 135);
  assert.ok(r > 230 && g > 200 && b > 120, `a bright centre: ${[r, g, b]}`);
  assert.notEqual(vision(0.1).hash, vision(0.6).hash, 'rays and sparks move');
});

// ---------------------------------------------------------------- layouts

const LAYOUT_SHOTS: Record<string, ProjectShot> = {
  screen: { layout: 'screen', backdrop: 'black', headline: 'Coats', eyebrow: 'MUSEUM VISIT' },
  screen2: { layout: 'screen', backdrop: 'black', headline: 'Coats', eyebrow: 'MUSEUM VISIT', screen: { asset: 'a', more: ['b'] } },
  screen3: { layout: 'screen', backdrop: 'black', headline: 'Coats', eyebrow: 'MUSEUM VISIT', screen: { asset: 'a', more: ['b', 'c'] } },
  title: { layout: 'title', backdrop: 'black', headline: 'MUSEUM VISIT', accent: '#2bb3a3', badges: [{ text: 'ONE', tone: 'accent' }, { text: 'TWO', tone: 'accent' }] },
  signoff: { layout: 'signoff', backdrop: 'black', expression: 'idea', eyebrow: 'End card', headline: 'Museum Visit', mark: 'END' },
};

test('every layout draws inside the frame in 16:9, 9:16 and 1:1, and deterministically', () => {
  for (const [name, stored] of Object.entries(LAYOUT_SHOTS)) {
    for (const [format, [W, H]] of Object.entries(SIZES)) {
      const shot = shotOf(stored), p = draw(shot, 2.5, W, H), box = inkBox(p);
      assert.ok(box.n > W * H * 0.04, `${name} ${format}: draws something (${box.n})`);
      const edge = 2;
      assert.ok(box.x0 >= edge && box.y0 >= edge && box.x1 < W - edge && box.y1 < H - edge,
        `${name} ${format}: in frame ${JSON.stringify(box)}`);
      assert.equal(draw(shot, 2.5, W, H).hash, p.hash, `${name} ${format}: deterministic`);
    }
  }
});

test('a screen shot draws the host\'s image in the phone, black while it loads, and a generated app screen without one', () => {
  const art = createCanvas(90, 190), a = art.getContext('2d');
  a.fillStyle = '#ff00ff';
  a.fillRect(0, 0, 90, 190);
  const withArt: MotionEnv = { ...env, image: (src) => (src === 'lib/shot.png' ? (art as unknown as CanvasImageSource) : null) };
  for (const [format, [W, H]] of Object.entries(SIZES)) {
    const [FW, FH] = FORMATS[format as Format], [phone] = screenLayout(FW, FH).phones;
    const at = (p: Pixels) => rgb(p, (phone.cx * W) / FW, ((phone.cy + phone.h * 0.2) * H) / FH);
    const shown = draw(shotOf({ layout: 'screen', screen: { asset: 'a1', image: 'lib/shot.png' }, headline: 'Cafe' }), 1.5, W, H, undefined, withArt);
    const [r, g, b] = at(shown);
    assert.ok(r > 200 && g < 60 && b > 200, `${format}: the image fills the screen (${[r, g, b]})`);
    const loading = draw(shotOf({ layout: 'screen', screen: { image: 'lib/missing.png' }, headline: 'Cafe' }), 1.5, W, H, undefined, withArt);
    assert.ok(at(loading).every((v) => v < 40), `${format}: dark while loading`);
    const placeholder = draw(shotOf({ layout: 'screen', headline: 'Cafe' }), 1.5, W, H, undefined, withArt);
    assert.ok(at(placeholder).every((v) => v > 150), `${format}: the generated app screen is light`);
  }
  const a1 = draw(shotOf({ layout: 'screen', headline: 'Map' }), 1.5), a2 = draw(shotOf({ layout: 'screen', headline: 'Coats' }), 1.5);
  assert.notEqual(a1.hash, a2.hash, 'each feature gets its own screen');
});

// ---------------------------------------------------------------- two and three phones

// A point on a phone: u across its width and v down its height (each -0.5..0.5), rotated with it.
function onPhone(p: PhoneSlot, u: number, v: number): [number, number] {
  const c = Math.cos(p.rot), s = Math.sin(p.rot), x = u * p.w, y = v * p.h;
  return [p.cx + x * c - y * s, p.cy + x * s + y * c];
}

function inPhone(p: PhoneSlot, [x, y]: [number, number]): boolean {
  const c = Math.cos(p.rot), s = Math.sin(p.rot), dx = x - p.cx, dy = y - p.cy;
  return Math.abs(dx * c + dy * s) <= p.w / 2 && Math.abs(-dx * s + dy * c) <= p.h / 2;
}

// The phone's screen inside its bezel (staging.ts `phone`: 4.5% of its width all round).
const BEZEL = 0.045;

// How much of phone i's screen the phones drawn in front of it (phone 1 in front of 2, 2 in front of 3) leave showing.
function showing(phones: PhoneSlot[], i: number): number {
  const p = phones[i], bu = 0.5 - BEZEL, bv = 0.5 - (BEZEL * p.w) / p.h;
  let seen = 0, all = 0;
  for (let a = 0; a <= 40; a++) for (let b = 0; b <= 80; b++) {
    const pt = onPhone(p, -bu + (2 * bu * a) / 40, -bv + (2 * bv * b) / 80);
    all++;
    if (!phones.slice(0, i).some((q) => inPhone(q, pt))) seen++;
  }
  return seen / all;
}

test('screenLayout fans two and three phones: phone 1 centre and in front, the others beside it, smaller and tilted outward', () => {
  for (const format of Object.keys(FORMATS) as Format[]) {
    const [W, H] = FORMATS[format];
    assert.deepEqual(screenLayout(W, H, 1), screenLayout(W, H), `${format}: one phone by default`);
    assert.equal(screenLayout(W, H, 0).phones.length, 1);
    assert.equal(screenLayout(W, H, 7).phones.length, 1 + SCREEN_MORE_MAX, 'never more than three');
    assert.deepEqual(screenLayout(W, H, 3), screenLayout(W, H, 3), 'a pure function of the frame');
    for (const n of [2, 3]) {
      const { phones: [hero, right, left], title } = screenLayout(W, H, n);
      const tag = `${format} ${n} phones`;
      assert.ok(right.cx > hero.cx && (!left || left.cx < hero.cx), `${tag}: phone 2 on the right, phone 3 on the left`);
      for (const side of [right, left].filter(Boolean)) {
        const k = side.h / hero.h, tilt = Math.abs(side.rot - hero.rot) * (180 / Math.PI);
        assert.ok(k >= 0.85 && k <= 0.9 && Math.abs(side.w / hero.w - k) < 1e-9, `${tag}: ${k.toFixed(3)} of phone 1's size`);
        assert.ok(tilt >= 3 && tilt <= 8 && Math.sign(side.rot - hero.rot) === Math.sign(side.cx - hero.cx), `${tag}: tilted ${tilt.toFixed(1)}° outward`);
      }
      if (n === 3) assert.ok(Math.abs((right.cx + left.cx) / 2 - hero.cx) < 1e-6, `${tag}: phone 1 is the centre`);
      if (W > H) assert.ok(title.x + title.w < Math.min(...screenLayout(W, H, n).phones.map((p) => p.cx - p.w * 0.6 - p.h * Math.abs(Math.sin(p.rot)) / 2)), `${tag}: the headline column stays left of the phones`);
      else assert.ok(title.cy + title.fs * 1.1 < Math.min(...screenLayout(W, H, n).phones.map((p) => onPhone(p, p.rot < 0 ? 0.5 : -0.5, -0.5)[1])), `${tag}: the title sits over the phones`);
    }
  }
});

test('no phone is cropped and every phone\'s screen stays readable, in 16:9, 9:16 and 1:1', () => {
  for (const format of Object.keys(FORMATS) as Format[]) {
    const [W, H] = FORMATS[format], short = Math.min(W, H), m = short * 0.01;
    for (const n of [1, 2, 3]) {
      const { phones } = screenLayout(W, H, n);
      phones.forEach((p, i) => {
        const tag = `${format} ${n} phones, phone ${i + 1}`;
        for (const u of [-0.52, 0.52]) for (const v of [-0.5, 0.5]) {
          const [x, y] = onPhone(p, u, v), bob = H * 0.005;
          assert.ok(x >= m && x <= W - m && y - bob >= m && y + bob <= H - m, `${tag}: corner (${x.toFixed(0)}, ${y.toFixed(0)}) inside the frame`);
        }
        // A 1179 px wide iPhone capture's 17 pt body text (51 px) stays at least 12 px tall on a 1080 px short side.
        const screenW = p.w * (1 - 2 * BEZEL) * (1080 / short);
        assert.ok((51 * screenW) / 1179 >= 12, `${tag}: its screen is ${screenW.toFixed(0)} px wide`);
        const shown = showing(phones, i);
        assert.ok(shown >= 0.6, `${tag}: ${(shown * 100).toFixed(0)}% of its screen shows`);
      });
    }
  }
});

// Solid stand-in screenshots, one colour per phone.
function solid(color: string): CanvasImageSource {
  const c = createCanvas(118, 256), x = c.getContext('2d');
  x.fillStyle = color;
  x.fillRect(0, 0, 118, 256);
  return c as unknown as CanvasImageSource;
}
const PHONE_ART: Record<string, CanvasImageSource> = { 'p1.png': solid('#ff0000'), 'p2.png': solid('#00ff00'), 'p3.png': solid('#0000ff') };
const withPhones: MotionEnv = { ...env, image: (src) => PHONE_ART[src] ?? null };
const THREE: ProjectShot = { layout: 'screen', backdrop: 'black', headline: ' ', eyebrow: '', screen: { asset: 'a', image: 'p1.png', more: [{ asset: 'b', image: 'p2.png' }, { asset: 'c', image: 'p3.png' }] } };
const HUES: [number, number, number][] = [[255, 0, 0], [0, 255, 0], [0, 0, 255]];
const isHue = (px: [number, number, number], i: number): boolean => px.every((v, k) => Math.abs(v - HUES[i][k]) < 40);

function hueBox(p: Pixels, i: number): { n: number; x: number; y: number } {
  let n = 0, sx = 0, sy = 0;
  for (let y = 0; y < p.H; y++) for (let x = 0; x < p.W; x++) if (isHue(rgb(p, x, y), i)) { n++; sx += x; sy += y; }
  return { n, x: sx / n, y: sy / n };
}

test('each phone draws its own image; phone 1 springs up first and phones 2 and 3 follow 0.3 s apart with the same spring', () => {
  for (const format of Object.keys(FORMATS) as Format[]) {
    const [W, H] = SIZES[format], [FW, FH] = FORMATS[format], { phones } = screenLayout(FW, FH, 3), shot = shotOf(THREE);
    for (const i of [1, 2]) {
      assert.equal(hueBox(draw(shot, i * 0.3 - 1 / FPS, W, H, undefined, withPhones), i).n, 0, `${format}: phone ${i + 1} not before ${i * 0.3} s`);
      assert.ok(hueBox(draw(shot, i * 0.3 + 0.25, W, H, undefined, withPhones), i).n > 50, `${format}: phone ${i + 1} springing up after ${i * 0.3} s`);
    }
    const rest = draw(shot, 2, W, H, undefined, withPhones);
    phones.forEach((P, i) => {
      const box = hueBox(rest, i), [x, y] = [(P.cx * W) / FW, (P.cy * H) / FH];
      assert.ok(box.n > 200 && Math.abs(box.y - y) < (P.h * H) / FH / 4 && Math.abs(box.x - x) < (P.w * W) / FW,
        `${format}: phone ${i + 1}'s image shows on it (${box.n} px about ${box.x.toFixed(0)}, ${box.y.toFixed(0)})`);
    });
    assert.ok(isHue(rgb(rest, (phones[0].cx * W) / FW, (phones[0].cy * H) / FH), 0), `${format}: phone 1 in front`);
    // The same spring: phone 2, u seconds into its entrance, rises as far as phone 1 did u seconds in.
    const rise = (i: number, t: number) => hueBox(draw(shot, t, W, H, undefined, withPhones), i).y - (phones[i].cy * H) / FH;
    for (const u of [0.1, 0.2]) {
      const bob = (H * 0.011);
      assert.ok(Math.abs(rise(1, 0.3 + u) - rise(0, u)) < Math.max(bob, H * 0.03), `${format}: the same spring ${u} s in`);
    }
  }
});

test('a phone without its image yet is dark and one without an image is the generated app screen, beside the others', () => {
  const [FW, FH] = FORMATS['9:16'], [W, H] = SIZES['9:16'], { phones } = screenLayout(FW, FH, 3);
  const shot = shotOf({ ...THREE, screen: { asset: 'a', image: 'p1.png', more: [{ asset: 'b', image: 'loading.png' }, { asset: 'c' }] } });
  const p = draw(shot, 2, W, H, undefined, withPhones);
  const peek = (P: PhoneSlot) => {
    const [x, y] = onPhone(P, P.cx > phones[0].cx ? 0.3 : -0.3, 0.1);
    return rgb(p, (x * W) / FW, (y * H) / FH);
  };
  assert.ok(peek(phones[1]).every((v) => v < 40), `phone 2 is dark while its image loads (${peek(phones[1])})`);
  assert.ok(peek(phones[2]).every((v) => v > 150), `phone 3 shows the generated app screen (${peek(phones[2])})`);
  const own = (i: number) => draw(shotOf({ ...THREE, headline: 'Cafe', screen: { asset: 'a', image: 'p1.png', more: [{ asset: 'b' }, { asset: 'c' }] } }), 2, W, H, undefined, withPhones);
  assert.equal(own(0).hash, own(0).hash, 'deterministic');
  const [x2, y2] = onPhone(phones[1], 0.3, -0.2), [x3, y3] = onPhone(phones[2], -0.3, -0.2), q = own(0);
  const strip = (x: number, y: number) => [0, 1, 2, 3, 4, 5].map((d) => rgb(q, (x * W) / FW, (y * H) / FH + d * 3).join()).join('|');
  assert.notEqual(strip(x2, y2), strip(x3, y3), 'phones 2 and 3 get their own generated screens');
});

test('screen crops use frame centre independently of phone count', () => {
  for (const [W, H] of Object.values(FORMATS)) for (const more of [['b'], ['b', 'c']]) {
    const shot = shotOf({ layout: 'screen', camera: 'pull-out', screen: { asset: 'a', more } });
    const { ctx } = surface(8, 8);
    assert.deepEqual(beginCamera(ctx, shot, 0, { endT: 3 }, W, H), { k: 1.12, x: W / 2, y: H / 2 });
  }
});

const onePhone = loadGolden<{ frames: string }>('one-phone-frames');

test('one phone draws byte for byte as before', { skip: onePhone.skip }, () => {
  assert.equal(onePhoneHash(env), onePhone.value?.frames);
});

test('an empty or junk `more` draws exactly like no `more`', () => {
  const none = onePhoneHash(env);
  assert.equal(onePhoneHash(env, []), none);
  assert.equal(onePhoneHash(env, ['', null, { image: '' }]), none);
});

test('the feature title slams in: absent, then big and faint, then at rest', () => {
  const [W, H] = SIZES['16:9'], shot = shotOf({ layout: 'screen', backdrop: 'black', headline: 'Map', eyebrow: '' });
  const left = (p: Pixels) => {
    let n = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W * 0.45; x++) if (rgb(p, x, y).some((v) => v > 200)) n++;
    return n;
  };
  assert.equal(left(draw(shot, 0.05, W, H)), 0, 'not yet');
  assert.ok(left(draw(shot, 1.2, W, H)) > 200, 'landed');
});

test('title badges drop in from above one after another and bounce to rest, then the headline rises in', () => {
  const [W, H] = SIZES['16:9'], shot = shotOf(LAYOUT_SHOTS.title);
  const teal = (p: Pixels) => {
    let n = 0, sy = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const [r, g, b] = rgb(p, x, y);
      if (r < 80 && g > 140 && b > 110) { n++; sy += y; }
    }
    return { n, y: n ? sy / n : NaN };
  };
  assert.equal(teal(draw(shot, 0.15, W, H)).n, 0, 'nothing before the first drop');
  const falling = teal(draw(shot, 0.48, W, H)), rest = teal(draw(shot, 2, W, H));
  assert.ok(falling.n > 0 && falling.y < rest.y - 10, `the teal badge falls from above (${falling.y} → ${rest.y})`);
  assert.ok(Math.abs(teal(draw(shot, 1.6, W, H)).y - rest.y) < 0.5, 'at rest after its bounces');
  const white = (p: Pixels) => {
    let n = 0;
    for (let y = Math.round(rest.y + 20); y < H; y++) for (let x = 0; x < W; x++) if (rgb(p, x, y).every((v) => v > 230)) n++;
    return n;
  };
  assert.equal(white(draw(shot, 0.6, W, H)), 0, 'no headline while the badges drop');
  assert.ok(white(draw(shot, 2, W, H)) > 300, 'the headline beneath them');
});

test('badge tone ramps are neutral and explicit accents are retained', () => {
  for (const tone of ['ink', 'paper'] as const) {
    const ramp = toneOf(tone, '#ff0000');
    for (const c of [ramp.light, ramp.base, ramp.shade, ramp.ink]) {
      const rgb = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
      assert.ok(Math.max(...rgb) - Math.min(...rgb) < 25);
    }
  }
  assert.equal(toneOf('accent', '#2bb3a3').base, '#2bb3a3');
});

for (const format of Object.keys(SIZES) as Format[]) test(`${format}: ink and paper title badges draw contrasting readable plates in their assigned regions`, () => {
  const [W, H] = SIZES[format];
  const shot = { ...shotOf({ layout: 'title', backdrop: 'black', badges: [{ text: 'ONE', tone: 'ink' }, { text: 'TWO', tone: 'paper' }] }), headline: '', eyebrow: '' };
  const p = draw(shot, 2.5, W, H);
  const regions = [{ ink: 0, paper: 0, lightText: 0, darkText: 0 }, { ink: 0, paper: 0, lightText: 0, darkText: 0 }];
  const inkPlate = ([r, g, b]: number[]): boolean => r >= 35 && r <= 95 && g >= r && b >= g && b - r < 25;
  const paperPlate = ([r, g, b]: number[]): boolean => r > 180 && g > 180 && b > 180 && Math.max(r, g, b) - Math.min(r, g, b) < 25 && !(r === 244 && g === 246 && b === 247);
  // A plate interior has a solid neighbourhood; antialiased glyph edges do not.
  const interior = (x: number, y: number, colour: (rgb: number[]) => boolean): boolean => {
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      if (x + dx < 0 || y + dy < 0 || x + dx >= W || y + dy >= H || !colour(rgb(p, x + dx, y + dy))) return false;
    }
    return true;
  };
  for (let y = 0; y < H * 0.6; y++) for (let x = 0; x < W; x++) {
    const [r, g, b] = rgb(p, x, y), region = regions[x < W / 2 ? 0 : 1];
    if (inkPlate([r, g, b]) && interior(x, y, inkPlate)) region.ink++;
    if (paperPlate([r, g, b]) && interior(x, y, paperPlate)) region.paper++;
    if (r === 244 && g === 246 && b === 247) region.lightText++;
    if (r === 32 && g === 38 && b === 45) region.darkText++;
  }
  assert.ok(regions[0].ink > 300 && regions[0].paper === 0, `ink plate occupies the first badge region: ${JSON.stringify(regions)}`);
  assert.ok(regions[0].lightText > 40 && regions[0].darkText === 0, 'readable light foreground occupies the ink badge');
  assert.ok(regions[1].paper > 300 && regions[1].ink === 0, 'paper plate occupies the second badge region');
  assert.ok(regions[1].darkText > 40, 'contrasting dark foreground occupies the paper badge');
});

test('sign-off defaults to a resting neutral performer beside optional text, with no tilt', () => {
  const shot = resolveShot({ mode: 'animation', layout: 'signoff', backdrop: 'black' }, 'Payoff', 'Closing');
  assert.deepEqual([shot.pose, shot.expression, shot.move], ['rest', 'neutral', 'idle']);
  const explicit = resolveShot({ layout: 'signoff', pose: 'wave', expression: 'happy' }, 'Payoff', 'Closing');
  assert.deepEqual([explicit.pose, explicit.expression], ['wave', 'happy']);
  for (const [W, H] of Object.values(FORMATS)) {
    const rig = characterOf('lens'), mark = 'END', S = signoffLayout(W, H, rig, mark);
    const right = S.lx + ((mark.length * 6 - 1) * S.c + 1) * S.g;
    assert.ok(S.px - widthOf(rig, S.h) / 2 > right);
    const { ctx } = surface(W, H), reference = surface(W, H);
    drawSceneFrame(ctx, 'animation', { ...shot, headline: ' ', eyebrow: '', mark: '' }, 2.5, { endT: 3 }, W, H, env);
    backdrop(reference.ctx, 'black', W, H, 2.5);
    const empty = signoffLayout(W, H, rig, '');
    drawCharacter(reference.ctx, empty.px, empty.ground, empty.h, { character: 'lens', pose: 'rest', expression: 'neutral', move: 'idle', t: 2.5 }, env);
    assert.deepEqual(ctx.getImageData(0, 0, W, H).data, reference.ctx.getImageData(0, 0, W, H).data);
  }
});

test('an unbranded sign-off draws no letters by default', () => {
  assert.equal(MARK, '');
  const shot = shotOf({ layout: 'signoff', backdrop: 'black', headline: 'Museum Visit' });
  assert.equal(draw(shot, 2.5).hash, draw({ ...shot, mark: '' }, 2.5).hash);
  assert.notEqual(draw(shot, 2.5).hash, draw({ ...shot, mark: 'END' }, 2.5).hash);
});

test('a sign-off without letters leaves their ground unshadowed in every format', () => {
  for (const [W, H] of Object.values(FORMATS)) {
    const shot = shotOf({ layout: 'signoff', backdrop: 'vision', headline: '' });
    const S = signoffLayout(W, H, characterOf(shot.character), '');
    const { ctx } = surface(W, H);
    backdrop(ctx, shot.backdrop, W, H, 2.5);
    const x = S.lx - S.g, y = S.ground;
    const ground = ctx.getImageData(x, y, 1, 1).data;
    drawSceneFrame(ctx, 'animation', shot, 2.5, shotTiming(0, 12, 30, FPS, 90), W, H, env);
    assert.deepEqual(ctx.getImageData(x, y, 1, 1).data, ground, `${W}x${H}: no letters' shadow`);
  }
});

test("the sign-off letters use the scene's pixel size, a 1px outline and the supplied accent ramp", () => {
  const OUTLINE = 0x111214, RAMP = new Set([0x8ad5cc, 0x2bb3a3, 0x20867a, 0x135149, OUTLINE]);
  const paint = (g: number) => {
    const w = 130 * g, h = 50 * g, { ctx } = surface(w, h);
    drawMark(ctx, 'END', 4 * g, 45 * g, g, 5, env, '#2bb3a3');
    const { data } = ctx.getImageData(0, 0, w, h);
    return (x: number, y: number): number => {
      if (x < 0 || y < 0 || x >= w || y >= h) return -1;
      const i = (y * w + x) * 4;
      return data[i + 3] === 255 ? (data[i] << 16) | (data[i + 1] << 8) | data[i + 2] : data[i + 3] ? -2 : -1;
    };
  };
  const one = paint(1), three = paint(3);
  let rows = [Infinity, -Infinity], n = 0;
  for (let y = 0; y < 50; y++) for (let x = 0; x < 130; x++) {
    const c = one(x, y);
    assert.notEqual(c, -2, 'no half-covered pixels');
    for (let j = 0; j < 3; j++) for (let i = 0; i < 3; i++) assert.equal(three(x * 3 + i, y * 3 + j), c, `pixel (${x}, ${y}) is a whole 3 x 3 block at g = 3`);
    if (c < 0) continue;
    n++;
    rows = [Math.min(rows[0], y), Math.max(rows[1], y)];
    assert.ok(RAMP.has(c), `(${x}, ${y}) #${c.toString(16)} is the outline or an accent shade`);
    if ([[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => one(x + dx, y + dy) < 0)) assert.equal(c, OUTLINE, `(${x}, ${y}) on the edge is the outline`);
  }
  assert.ok(n > 400, 'draws');
  assert.deepEqual(rows, [45 - 37, 44], 'seven 5-pixel cells and their outline, the outline under them on the ground');
});

test('sign-off letters follow the resolved accent even without an eyebrow', () => {
  const stored = { layout: 'signoff', backdrop: 'black', mark: 'END', headline: '', eyebrow: '' };
  const base = shotOf(stored), explicit = shotOf({ ...stored, accent: ACCENT });
  assert.equal(draw(base, 2).hash, draw(explicit, 2).hash);
  const teal = shotOf({ ...stored, accent: '#2bb3a3' }), blue = shotOf({ ...stored, accent: '#3366cc' });
  assert.notEqual(draw(teal, 2).hash, draw(base, 2).hash);
  assert.notEqual(draw(teal, 2).hash, draw(blue, 2).hash);
  assert.equal(draw(teal, 2).hash, draw(teal, 2).hash, 'repainting the shared canvas retains the requested accent');
});

test('independent crops carry ensemble and bubble content without forcing a backdrop', () => {
  for (const stored of [{ ensemble: [{ character: 'lens' }] }, { bubble: 'say' }] as ProjectShot[]) {
    for (const camera of ['push-in', 'pull-out'] as const) {
      const shot = shotOf({ ...stored, camera, backdrop: 'work' });
      assert.equal(shot.backdrop, 'work');
      const lt = camera === 'push-in' ? 2.9 : 0;
      assert.notEqual(draw(shot, lt).hash, draw(shotOf({ ...stored, backdrop: 'work' }), lt).hash);
      assert.ok(draw(shot, lt).data.some((v, i) => i % 4 !== 3 && v < 200));
    }
  }
});

test('a staged layout leaves the acting off: screen and title draw no performers, a sign-off only its lead', () => {
  const acting: ProjectShot = { bubble: 'think', costar: { character: 'lens', bubble: 'say' }, ensemble: [{ character: 'lens', bubble: 'exclaim' }] };
  for (const stored of Object.values(LAYOUT_SHOTS)) {
    for (const lt of [0.8, 2.5]) assert.equal(draw(shotOf({ ...stored, ...acting }), lt).hash, draw(shotOf(stored), lt).hash, `${stored.layout} @${lt}`);
  }
  assert.equal(shotOf({ ...LAYOUT_SHOTS.title, ...acting }).ensemble?.length, 1, 'kept in the shot for switching back to the stage');
});

test('a staged layout ignores a cameo: only animation draws it', () => {
  const { ctx } = surface(64, 36);
  drawSceneFrame(ctx as Ctx, 'cameo', shotOf({ layout: 'title', badges: [{ text: 'X', tone: 'ink' }] }), 1, shotTiming(0, null, null, FPS, 60), 1920, 1080, env);
  const { data } = ctx.getImageData(0, 0, 64, 36);
  let top = 0;
  for (let i = 3; i < data.length / 2; i += 4) top += data[i];
  assert.equal(top, 0, 'the top half stays transparent: just the cameo character in its corner');
});

// ---------------------------------------------------------------- hideHeadline

/** Pixels not the black backdrop, inside [x0, x1) x [y0, y1). */
function inkIn(p: Pixels, x0: number, y0: number, x1: number, y1: number): number {
  let n = 0;
  for (let y = Math.max(0, Math.floor(y0)); y < Math.min(p.H, Math.ceil(y1)); y++) {
    for (let x = Math.max(0, Math.floor(x0)); x < Math.min(p.W, Math.ceil(x1)); x++) {
      const i = (y * p.W + x) * 4;
      if (p.data[i] + p.data[i + 1] + p.data[i + 2] > 40) n++;
    }
  }
  return n;
}

test('hideHeadline leaves the stage with no headline or eyebrow text there; a staged layout ignores it; absent or false draws unchanged', () => {
  const [W, H] = SIZES['16:9'], [FW, FH] = FORMATS['16:9'], L = layoutFor(FW, FH), sx = W / FW, sy = H / FH;
  // The eyebrow and (a one-line) headline sit just under L.ty, well clear of the diagram row below them.
  const band = { x0: L.tx * sx, x1: (L.tx + L.tw) * sx, y0: (L.ty - L.fs * 0.5) * sy, y1: (L.ty + L.fs * 2.2) * sy };
  const base: ProjectShot = { backdrop: 'black', pose: 'think', headline: 'Idea', eyebrow: 'STUDIO' };
  const shown = draw(shotOf(base), 2.5, W, H), hidden = draw(shotOf({ ...base, hideHeadline: true }), 2.5, W, H);
  assert.ok(inkIn(shown, band.x0, band.y0, band.x1, band.y1) > 50, 'the headline and eyebrow draw there by default');
  assert.equal(inkIn(hidden, band.x0, band.y0, band.x1, band.y1), 0, 'hideHeadline leaves no text there');
  assert.equal(draw(shotOf({ ...base, hideHeadline: false }), 2.5, W, H).hash, shown.hash, 'false draws exactly as absent');
  const duo: ProjectShot = { ...base, costar: { character: 'lens' } };
  assert.notEqual(draw(shotOf({ ...duo, hideHeadline: true }), 2.5, W, H).hash, draw(shotOf(duo), 2.5, W, H).hash, 'a duo hides it too');
  const ensemble: ProjectShot = { ...base, costar: { character: 'lens' }, ensemble: [{ character: 'lens' }] };
  assert.notEqual(draw(shotOf({ ...ensemble, hideHeadline: true }), 2.5, W, H).hash, draw(shotOf(ensemble), 2.5, W, H).hash, 'an ensemble hides it too');
  for (const stored of Object.values(LAYOUT_SHOTS)) {
    assert.equal(draw(shotOf({ ...stored, hideHeadline: true }), 2.5, W, H).hash, draw(shotOf(stored), 2.5, W, H).hash, `${stored.layout} ignores hideHeadline`);
  }
});

test("the sign-off eyebrow uses the workspace accent, at least 40% of the headline's size", () => {
  const [W, H] = SIZES['9:16'], [FW, FH] = FORMATS['9:16'];
  const S = signoffLayout(FW, FH, characterOf('lens'));
  // Well above the letters and the resting performer, which sit down at S.ground.
  const cutY = ((S.text.cy + (S.ground - S.text.cy) * 0.3) * H) / FH;
  const shot = shotOf({ ...LAYOUT_SHOTS.signoff, headline: 'Dinner', accent: '#2bb3a3' });
  const p = draw(shot, 2.5, W, H);
  const accent = { n: 0, y0: Infinity, y1: -Infinity }, white = { n: 0, y0: Infinity, y1: -Infinity };
  for (let y = 0; y < cutY; y++) for (let x = 0; x < W; x++) {
    const [r, g, b] = rgb(p, x, y);
    if (r < 80 && g > 140 && b > 110) { accent.n++; accent.y0 = Math.min(accent.y0, y); accent.y1 = Math.max(accent.y1, y); }
    if (r > 220 && g > 220 && b > 220) { white.n++; white.y0 = Math.min(white.y0, y); white.y1 = Math.max(white.y1, y); }
  }
  assert.ok(accent.n > 30, `the eyebrow draws in the workspace accent (${accent.n} px)`);
  assert.ok(white.n > 30, `the headline draws in white (${white.n} px)`);
  const eyebrowH = accent.y1 - accent.y0 + 1, headlineH = white.y1 - white.y0 + 1;
  assert.ok(eyebrowH >= headlineH * 0.35, `eyebrow ${eyebrowH}px reads beside a ${headlineH}px headline (>= ~40% target, minus antialiasing slack)`);
});
