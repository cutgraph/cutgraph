import { characterOf as defaultRigOf, DEFAULT_CHARACTER as defaultRigId } from '../src/index.ts';
const DEFAULT_RIG = defaultRigOf(defaultRigId);
// Acting: bubbles over any performer, ensemble line-ups and entrances from the right. Everything
// here is opt-in: a shot without a bubble, an ensemble or a right-side entrance draws as before (the
// single-frame goldens in duo.test.ts hold the single path to main byte for byte).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  BUBBLES, ENSEMBLE_MAX, FORMATS, bubbleBeat, bubbleLayout, costarTime, crownOf, drawBubble, drawCharacter, drawSceneFrame, duoLayout,
  enterFromRight, ensembleLayout, layoutFor, memberTime, pixelOf, resolveShot, widthOf,
} from '../src/index.ts';
import type { BubbleId, Ctx, Format, Shot, Spot } from '../src/index.ts';
import { BUBBLE_GOLD } from '../src/acting.ts';
import { CHARACTERS } from '../src/characters.ts';
import { after } from 'node:test';
import { diagramRow } from '../src/scene.ts';
import { nodeEnv } from '../scripts/node-env.ts';
import { render } from './helpers.ts';

import { paletteRig } from './palette-rig.ts';
const SYNTHETIC = paletteRig('synthetic-rig', true);
Object.assign(CHARACTERS, { [SYNTHETIC.id]: SYNTHETIC });
after(() => { Reflect.deleteProperty(CHARACTERS, SYNTHETIC.id); });
const env = nodeEnv();
const FORMAT_IDS = Object.keys(FORMATS) as Format[];
const TIMING = { firstT: 50, cueT: 1 };

const LINE_UP = {
  mode: 'animation', character: 'lens', pose: 'rest', expression: 'curious', move: 'idle', diagram: 'none', backdrop: 'work',
  costar: { character: SYNTHETIC.id, palette: 'teal', pose: 'rest', expression: 'neutral', move: 'nod', bubble: 'exclaim' },
  ensemble: [
    { character: 'lens', pose: 'wave', expression: 'happy', move: 'wave' },
    { character: SYNTHETIC.id, palette: 'violet', pose: 'point', expression: 'focused', move: 'idle' },
  ],
};
const LINE_UP_RIGS = [DEFAULT_RIG, SYNTHETIC, DEFAULT_RIG, SYNTHETIC];

// Every performer's spot in the three layouts bubbles appear in: the single lead, a duo, an ensemble of four.
function spots(W: number, H: number): { spot: Spot; rig: Character }[] {
  const L = layoutFor(W, H), D = duoLayout(W, H, DEFAULT_RIG, SYNTHETIC, true), E = ensembleLayout(W, H, [DEFAULT_RIG, SYNTHETIC, SYNTHETIC, SYNTHETIC]);
  return [
    { spot: { x: L.characterX, by: L.characterY, h: L.characterH }, rig: DEFAULT_RIG },
    { spot: D.lead, rig: DEFAULT_RIG }, { spot: D.costar, rig: SYNTHETIC },
    ...E.slots.map((spot, i) => ({ spot, rig: i ? SYNTHETIC : DEFAULT_RIG })),
  ];
}

function inkBox(ctx: Ctx, W: number, H: number): { n: number; x0: number; y0: number; x1: number; y1: number } {
  const { data } = ctx.getImageData(0, 0, W, H);
  const box = { n: 0, x0: W, y0: H, x1: -1, y1: -1 };
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    if (!data[(y * W + x) * 4 + 3]) continue;
    box.n++;
    box.x0 = Math.min(box.x0, x); box.x1 = Math.max(box.x1, x); box.y0 = Math.min(box.y0, y); box.y1 = Math.max(box.y1, y);
  }
  return box;
}

test('resolveShot keeps a bubble it can draw on the lead, the co-star and each ensemble member, and nothing else', () => {
  const plain = resolveShot({ mode: 'animation', costar: { character: 'lens' } }, 'Beat', 'x');
  assert.ok(!('bubble' in plain) && !('ensemble' in plain) && !('bubble' in plain.costar!), 'absent stays absent');
  const s = resolveShot({ bubble: 'think', costar: { bubble: 'say' }, ensemble: [{ bubble: 'exclaim' }, { bubble: 'sing' }] }, 'Beat', 'x');
  assert.equal(s.bubble, 'think');
  assert.equal(s.costar?.bubble, 'say');
  assert.deepEqual(s.ensemble?.map((m) => m.bubble), ['exclaim', undefined]);
  assert.equal(resolveShot({ bubble: 'shout' }, 'Beat', 'x').bubble, undefined, 'an unknown bubble is dropped');
  assert.deepEqual([...BUBBLES], ['think', 'say', 'exclaim', 'question', 'exclaims']);
});

test('an ensemble resolves each member like the co-star, at most ENSEMBLE_MAX, skipping what is not a member', () => {
  const members = [{ character: 'lens', palette: 'slate', pose: 'flex' }, null, 'guest', { character: 'nobody', move: 'moonwalk' }, {}, {}];
  const s = resolveShot({ mode: 'animation', ensemble: members as never }, 'Payoff', 'x');
  assert.equal(ENSEMBLE_MAX, 3);
  assert.deepEqual(s.ensemble, [
    { character: 'lens', palette: 'periwinkle', pose: 'flex', expression: 'happy', move: 'idle' },
    { character: 'nobody', palette: 'periwinkle', pose: 'rest', expression: 'happy', move: 'idle' },
    { character: 'lens', palette: 'periwinkle', pose: 'rest', expression: 'happy', move: 'idle' },
  ]);
  assert.equal(resolveShot({ ensemble: [] }, 'Beat', 'x').ensemble, undefined, 'an empty ensemble is none');
  assert.equal(s.interaction, undefined, 'an ensemble alone brings no interaction');
});

test('think: its dots appear one at a time, ". .. ...", then start over; say types its words; exclaim pops and shakes', () => {
  assert.equal(bubbleBeat('think', -0.01), null, 'nothing before it appears');
  const dots = [0, 0.2, 0.39, 0.4, 0.79, 0.8, 1.2, 1.59, 1.65, 2.1, 2.5].map((u) => bubbleBeat('think', u)!.dots);
  assert.deepEqual(dots, [1, 1, 1, 2, 2, 3, 3, 3, 1, 2, 3]);
  const words = [0, 0.13, 0.26, 0.39, 0.52, 0.9, 1.1].map((u) => bubbleBeat('say', u)!.words);
  assert.deepEqual(words, [1, 2, 3, 4, 5, 5, 1]);
  for (const kind of ['think', 'say'] as const) {
    assert.equal(bubbleBeat(kind, 0)!.scale, 0);
    assert.ok(bubbleBeat(kind, 0.25)!.scale > 1, `${kind} pops past its size`);
    assert.equal(bubbleBeat(kind, 0.3)!.scale, 1);
  }
  const bang = (u: number) => bubbleBeat('exclaim', u)!;
  assert.ok(bang(0.1).scale > 1.4 && bang(0.18).scale < 0.9 && bang(0.26).scale > 1, 'overshoot, dip, rebound');
  assert.deepEqual([bang(0.32).scale, bang(2).scale], [1, 1], 'then it holds');
  const shake = [0.2, 0.33, 0.38, 0.43, 0.6, 0.7, 3].map((u) => bang(u).shake);
  assert.deepEqual(shake, [0, -1, 1, -1, 1, 0, 0], 'a tiny shake right after the pop');
});

// The marks centred over the head: the "?" and the "!!".
const CENTRED: readonly BubbleId[] = ['question', 'exclaims'];

test('bubbles sit above the head, lean toward the centre (or, the "?" and "!!", centre on it) and stay inside the frame in every format and layout', () => {
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format];
    for (const { spot, rig } of spots(W, H)) {
      for (const kind of BUBBLES) {
        const b = bubbleLayout(kind, spot, rig, W, H), tag = `${format} ${kind} at x=${spot.x.toFixed(0)}`, centred = CENTRED.includes(kind);
        assert.ok(b.body.x >= 0 && b.body.y >= 0 && b.body.x + b.body.w <= W && b.body.y + b.body.h <= H, `${tag}: in frame`);
        assert.ok(b.body.y + b.body.h <= spot.by - (centred ? crownOf(rig, spot.h) : spot.h), `${tag}: above the head`);
        assert.equal(b.dir, spot.x > W / 2 ? -1 : 1, `${tag}: leans toward the centre`);
        if (centred) assert.ok(Math.abs(b.body.x + b.body.w / 2 - spot.x) <= 1, `${tag}: centred on the head`);
        else assert.ok(b.dir > 0 ? b.body.x + b.body.w / 2 >= spot.x : b.body.x + b.body.w / 2 <= spot.x, `${tag}: on the centre's side`);
        assert.ok(Number.isInteger(b.q) && b.q >= 2, `${tag}: whole screen pixels per cell`);
        for (const u of [0.1, 3]) {
          const f = render(W / 4, H / 4, (ctx) => {
            ctx.scale(0.25, 0.25);
            drawBubble(ctx, kind, spot, rig, u, W, H, env);
          });
          const box = inkBox(f.ctx, W / 4, H / 4);
          assert.ok(box.n > 0, `${tag} @${u}: drawn`);
          if (u === 3) assert.ok(box.x0 > 0 && box.y0 > 0 && box.x1 < W / 4 - 1 && box.y1 < H / 4 - 1, `${tag}: nothing clipped`);
        }
      }
    }
  }
});

test('a bubble keeps the house style: flat colours from the ramps, whole-pixel cells, ringed by the 1px outline', () => {
  const COLOURS = new Set(['#111214', '#2e333b', '#eef2f4', '#ffffff', '#c9d2d8', '#f5c518', '#ffeb8f', '#c98f0b']);
  const [W, H] = FORMATS['16:9'], D = duoLayout(W, H, DEFAULT_RIG, SYNTHETIC, true);
  for (const kind of BUBBLES) {
    for (const spot of [D.lead, D.costar]) {
      const f = render(W, H, (ctx) => drawBubble(ctx, kind, spot, spot === D.lead ? DEFAULT_RIG : SYNTHETIC, 1, W, H, env));
      const { data } = f.ctx.getImageData(0, 0, W, H), seen = new Set<string>();
      const solid = (x: number, y: number) => x >= 0 && y >= 0 && x < W && y < H && data[(y * W + x) * 4 + 3] === 255;
      let rim = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (!data[i + 3]) continue;
        assert.equal(data[i + 3], 255, `${kind}: opaque`);
        const hex = '#' + [0, 1, 2].map((k) => data[i + k].toString(16).padStart(2, '0')).join(''), x = (i / 4) % W, y = Math.floor(i / 4 / W);
        seen.add(hex);
        if ([[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => !solid(x + dx, y + dy))) {
          rim++;
          assert.equal(hex, '#111214', `${kind}: its edge at (${x}, ${y}) is the outline`);
        }
      }
      assert.ok(rim > 100, `${kind}: has an outline`);
      assert.ok([...seen].every((c) => COLOURS.has(c)), `${kind}: only its own colours, got ${[...seen].join(' ')}`);
    }
  }
  const b = bubbleLayout('think', D.lead, DEFAULT_RIG, W, H);
  assert.ok(b.q >= pixelOf(DEFAULT_RIG, D.lead.h), 'at least the rig’s own pixel size');
});

// Where each colour is in a frame: its pixels' count and bounding box.
function spread(data: Uint8ClampedArray, W: number, hexes: readonly string[]): { n: number; x0: number; x1: number; y0: number; y1: number } {
  const want = new Set(hexes), box = { n: 0, x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity };
  for (let i = 0; i < data.length; i += 4) {
    if (!data[i + 3] || !want.has('#' + [0, 1, 2].map((k) => data[i + k].toString(16).padStart(2, '0')).join(''))) continue;
    const x = (i / 4) % W, y = Math.floor(i / 4 / W);
    box.n++; box.x0 = Math.min(box.x0, x); box.x1 = Math.max(box.x1, x); box.y0 = Math.min(box.y0, y); box.y1 = Math.max(box.y1, y);
  }
  return box;
}

const NEUTRAL_MARK = ['#eef2f4', '#ffffff', '#c9d2d8'];

test('the "?" and the "!!" are centred over the performer\'s own head, just above its crown, in every format', () => {
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format];
    for (const { spot, rig } of spots(W, H)) {
      for (const kind of CENTRED) {
        const f = render(W, H, (ctx) => drawBubble(ctx, kind, spot, rig, 3, W, H, env)), box = inkBox(f.ctx, W, H), tag = `${format} ${kind} over ${rig.id} at ${spot.x.toFixed(0)}`;
        const crown = spot.by - crownOf(rig, spot.h), q = bubbleLayout(kind, spot, rig, W, H).q;
        assert.ok(Math.abs((box.x0 + box.x1 + 1) / 2 - spot.x) <= q / 2 + 1, `${tag}: centred (${box.x0}..${box.x1} over ${spot.x.toFixed(1)})`);
        assert.ok(box.y1 < crown && crown - box.y1 <= 3 * q, `${tag}: just above the crown (${box.y1} vs ${crown})`);
      }
    }
  }
  // The declared crown contains the registered character's moving head.
  const [W, H] = FORMATS['16:9'], D = duoLayout(W, H, DEFAULT_RIG, SYNTHETIC, true);
  for (const [rig, spot, look] of [[SYNTHETIC, D.costar, { pose: 'hips', move: 'idle' }], [DEFAULT_RIG, D.lead, { expression: 'idea', move: 'idle' }]] as const) {
    let top = Infinity;
    for (const t of [0, 0.4, 0.8, 1.2, 1.6, 2]) {
      top = Math.min(top, inkBox(render(W, H, (ctx) => drawCharacter(ctx, spot.x, spot.by, spot.h, { character: rig.id, ...look, t }, env)).ctx, W, H).y0);
    }
    const crown = spot.by - crownOf(rig, spot.h);
    assert.ok(top >= crown && top - crown < 5 * pixelOf(rig, spot.h), `${rig.id}: drawn from ${top}, its crown ${crown}`);
  }
});

test('paired marks use equal neutral ramps and pop and shake simultaneously', () => {
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format], spot = ensembleLayout(W, H, [DEFAULT_RIG, SYNTHETIC, SYNTHETIC, SYNTHETIC]).slots[1];
    const at = (u: number) => render(W, H, (ctx) => drawBubble(ctx, 'exclaims', spot, SYNTHETIC, u, W, H, env)).ctx.getImageData(0, 0, W, H).data;
    const counts = (data: Uint8ClampedArray) => {
      const left = [], right = [];
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const i = (y * W + x) * 4;
        if (data[i + 3]) (x < spot.x ? left : right).push([x, y, ...data.slice(i, i + 4)]);
      }
      return [left, right];
    };
    assert.equal(spread(at(-0.01), W, NEUTRAL_MARK).n, 0);
    for (const u of [0, 0.05, 0.1, 0.18, 0.35, 0.4, 0.45, 0.9, 3]) {
      const calls: number[][] = [];
      const rendered = render(W, H, (ctx) => {
        const original = ctx.drawImage.bind(ctx);
        ctx.drawImage = ((...args: Parameters<Ctx['drawImage']>) => {
          calls.push(args.slice(1) as number[]);
          original(...args);
        }) as Ctx['drawImage'];
        drawBubble(ctx, 'exclaims', spot, SYNTHETIC, u, W, H, env);
      });
      const data = rendered.ctx.getImageData(0, 0, W, H).data;
      assert.ok(calls.length === 0 || calls.length === 2, `${format} @${u}: simultaneous onset`);
      if (calls.length) {
        const [left, right] = calls;
        assert.deepEqual([left[2], left[3], left[5], left[6], left[7]], [right[2], right[3], right[5], right[6], right[7]], `${format} @${u}: same scale and vertical placement`);
      }
      assert.equal(spread(data, W, Object.values(BUBBLE_GOLD)).n, 0);
    }
    const [left, right] = counts(at(3)), dx = right[0][0] - left[0][0];
    assert.deepEqual(left.map(([x, ...rest]) => [x + dx, ...rest]), right, `${format}: equal neutral colours`);
    assert.ok(spread(at(3), W, NEUTRAL_MARK).n > 300);
    for (const u of [-0.01, 0.1, 0.18, 0.35, 2]) assert.deepEqual(bubbleBeat('exclaims', u), bubbleBeat('exclaim', u));
  }
  assert.deepEqual(BUBBLE_GOLD, { F: '#f5c518', L: '#ffeb8f', S: '#c98f0b' });
});

test('ensemble layout: lead first, every rig the same pixel size, fits every format, feet level, clear of the text', () => {
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format];
    for (const rigs of [[DEFAULT_RIG, SYNTHETIC, SYNTHETIC, SYNTHETIC], [SYNTHETIC, DEFAULT_RIG, DEFAULT_RIG], [DEFAULT_RIG, DEFAULT_RIG, DEFAULT_RIG, DEFAULT_RIG], [DEFAULT_RIG, SYNTHETIC, DEFAULT_RIG]]) {
      const E = ensembleLayout(W, H, rigs), row = diagramRow(E), tag = `${format} ${rigs.map((r) => r.id).join('+')}`;
      assert.equal(E.slots.length, rigs.length);
      assert.equal(new Set(E.slots.map((s) => s.by)).size, 1, `${tag}: feet level`);
      assert.equal(new Set(E.slots.map((s, i) => pixelOf(rigs[i], s.h))).size, 1, `${tag}: one pixel size`);
      const boxes = E.slots.map((s, i) => ({ l: s.x - widthOf(rigs[i], s.h) / 2, r: s.x + widthOf(rigs[i], s.h) / 2, top: s.by - s.h }));
      assert.ok(boxes[0].l >= W * 0.03 - 1e-6 && boxes[boxes.length - 1].r <= W * 0.97 + 1e-6, `${tag}: inside the frame`);
      boxes.slice(1).forEach((b, i) => assert.ok(b.l > boxes[i].r, `${tag}: ${i} and ${i + 1} apart`));
      assert.ok(boxes.every((b) => b.top > row.Y + row.bh && b.top > E.ty + E.fs * 1.12), `${tag}: heads below the headline and diagram`);
      assert.ok(E.slots[0].by < layoutFor(W, H).capY - layoutFor(W, H).capFs, `${tag}: feet above the captions`);
      assert.deepEqual([E.characterX, E.characterH], [E.slots[0].x, E.slots[0].h]);
    }
    const tall = ensembleLayout(W, H, [DEFAULT_RIG, SYNTHETIC]).slots, big = ensembleLayout(W, H, [DEFAULT_RIG, SYNTHETIC, SYNTHETIC, SYNTHETIC]).slots;
    assert.ok(Math.abs(tall[1].h / tall[0].h - SYNTHETIC.scale!) < 1e-9, `${format}: the Guest stands its declared scale over the default rig`);
    assert.ok(big[1].h <= tall[1].h, `${format}: a longer row shrinks, never grows`);
  }
});

// Exact body colours locate the alternating rigs within their own slots.
const RGB = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const TONES = { teal: RGB('#2bb3a3'), violet: RGB('#8b5cf6'), character: RGB('#9fbae9'), white: RGB('#eef2f4') };

// Per tone, how many pixels and their mean x, in rows [y0, y1) (all rows by default).
function centroids(data: Uint8ClampedArray, W: number, H: number, y0 = 0, y1 = H, x0 = 0, x1 = W): Record<keyof typeof TONES, { n: number; x: number }> {
  const out = Object.fromEntries(Object.keys(TONES).map((k) => [k, { n: 0, x: 0 }])) as Record<keyof typeof TONES, { n: number; x: number }>;
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
    const i = (y * W + x) * 4;
    for (const [k, [r, g, b]] of Object.entries(TONES)) {
      if (data[i] === r && data[i + 1] === g && data[i + 2] === b) { out[k as keyof typeof TONES].n++; out[k as keyof typeof TONES].x += x; }
    }
  }
  for (const v of Object.values(out)) v.x = v.n ? v.x / v.n : NaN;
  return out;
}

const draw = (shot: Shot, lt: number, W = 1920, H = 1080) => render(W, H, (ctx) => drawSceneFrame(ctx, 'animation', shot, lt, TIMING, W, H, env));
const frame = (shot: Shot, lt: number, W = 1920, H = 1080) => draw(shot, lt, W, H).ctx.getImageData(0, 0, W, H).data;

test('the ensemble alternates Lens and tall guests in a row, with the teal guest’s "!" above it', () => {
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format], shot = resolveShot(LINE_UP, 'Beat', 'Walk'), E = ensembleLayout(W, H, LINE_UP_RIGS);
    // Sample each body's band below the heads and the bubble.

    for (const [index, slot] of E.slots.entries()) {
      const data = frame(shot, 3, W, H);
      let pixels = 0;
      for (let y = Math.round(slot.by - slot.h / 2); y < slot.by; y++) {
        for (let x = Math.round(slot.x - slot.h / 4); x < slot.x + slot.h / 4; x++) {
          const i = (y * W + x) * 4;
          const [r, g, b] = [TONES.character, TONES.teal, TONES.character, TONES.violet][index];
          if (data[i] === r && data[i + 1] === g && data[i + 2] === b) pixels++;
        }
      }
      assert.ok(pixels > 200, `${format}: a character occupies each slot`);
    }
    const bang = bubbleLayout('exclaim', E.slots[1], SYNTHETIC, W, H);
    const inBang = (data: Uint8ClampedArray) => {
      let n = 0;
      for (let y = bang.body.y; y < bang.body.y + bang.body.h; y++) for (let x = bang.body.x; x < bang.body.x + bang.body.w; x++) {
        const i = (y * W + x) * 4;
        if (data[i] === 245 && data[i + 1] === 197 && data[i + 2] === 24) n++;
      }
      return n;
    };
    assert.equal(inBang(frame(shot, 0.9, W, H)), 0, `${format}: no "!" before the cue`);
    assert.ok(inBang(frame(shot, 3, W, H)) > 20, `${format}: the "!" over the teal performer after the cue`);
  }
  const duo = resolveShot({ ...LINE_UP, ensemble: undefined }, 'Payoff', 'Garden');
  assert.notEqual(draw(duo, 3).hash, draw(resolveShot(LINE_UP, 'Payoff', 'Garden'), 3).hash, 'an ensemble is not the duo');
  const shot = resolveShot({ ...LINE_UP, interaction: 'fistbump' }, 'Payoff', 'Garden');
  assert.equal(draw(shot, 1).hash, draw({ ...shot, interaction: 'none' }, 1).hash, 'an ensemble plays no interaction');
});

test('a performer right of centre whose move is enter walks in from the right edge, facing left, and stops on its spot', () => {
  const [W, H] = FORMATS['16:9'], D = duoLayout(W, H, DEFAULT_RIG, SYNTHETIC, false);
  const p = (t: number) => enterFromRight({ character: 'lens', move: 'enter', t }, SYNTHETIC, D.costar, 1, W);
  const xs = [-0.5, 0, 0.3, 0.6, 0.9, 1.2, 1.35, 1.5, 3].map((t) => p(t).x);
  assert.ok(xs[0] - widthOf(SYNTHETIC, D.costar.h) / 2 >= W && xs[1] === xs[0], 'off the right edge until it starts');
  assert.ok(xs.every((x, i) => i === 0 || x <= xs[i - 1]), `walks steadily left ${xs.map((x) => x.toFixed(0))}`);
  assert.equal(xs[xs.length - 1], D.costar.x, 'and stops on its spot');
  const steps = [0.3, 0.6, 0.9].map((t, i, a) => i && p(t).x - p(a[i - 1]).x).slice(1);
  assert.ok(Math.abs(steps[0] - steps[1]) < 1e-6, 'at a steady pace');
  assert.deepEqual([p(0.5).face, p(0.5).performer.move, p(3).face, p(3).performer.move], [-1, 'walk', -1, 'idle']);
  assert.ok(p(0.5).since < 0 && p(3).since > 0);
  const lead = enterFromRight({ character: 'lens', move: 'enter', t: 0.2 }, DEFAULT_RIG, D.lead, 1, W);
  assert.deepEqual([lead.x, lead.face, lead.performer.move, lead.since], [D.lead.x, 1, 'enter', Infinity], 'the lead keeps its own entrance');
  const idle = enterFromRight({ character: 'lens', move: 'idle', t: 0.2 }, SYNTHETIC, D.costar, 1, W);
  assert.equal(idle.x, D.costar.x);

  const shot = resolveShot({ mode: 'animation', character: 'lens', expression: 'curious', diagram: 'blank', costar: { character: SYNTHETIC.id, palette: 'teal', move: 'enter', bubble: 'say' } }, 'Beat', 'x');
  const at = (lt: number) => centroids(frame(shot, lt), W, H, 0, H, W / 2);
  assert.equal(at(0.1).teal.n, 0, 'the Guest is still off frame');
  const walking = [0.6, 0.9, 1.2].map((lt) => at(lt).teal.x);
  assert.ok(walking[0] > walking[1] && walking[1] > walking[2] && walking[0] > D.costar.x + 100, `walks in from the right ${walking}`);
  assert.ok(Math.abs(at(3).teal.x - D.costar.x) < 60, 'and stands on its spot');
  const talk = bubbleLayout('say', D.costar, SYNTHETIC, W, H);
  assert.ok(costarTime('enter', 1.2) < 1.39, 'the co-star enters a beat after the lead, still walking at 1.2 s');
  const whites = (lt: number) => {
    const data = frame(shot, lt);
    let n = 0;
    for (let y = talk.body.y; y < talk.body.y + talk.body.h; y++) for (let x = talk.body.x; x < talk.body.x + talk.body.w; x++) {
      const i = (y * W + x) * 4;
      if (data[i] === 238 && data[i + 1] === 242 && data[i + 2] === 244) n++;
    }
    return n;
  };
  assert.equal(whites(1.2), 0, 'it speaks only once it has arrived, not on the cue while walking');
  assert.ok(whites(2.5) > 1000, 'then its speech bubble shows');
});

test('the facing-left walk-in mirrors the rig about its own centre; ensemble members enter one after another', () => {
  const [W, H] = FORMATS['9:16'], shot = resolveShot({ ...LINE_UP, costar: { ...LINE_UP.costar, move: 'enter' },
    ensemble: LINE_UP.ensemble.map((m) => ({ ...m, move: 'enter' })) }, 'Payoff', 'x');
  const E = ensembleLayout(W, H, LINE_UP_RIGS);
  assert.ok(E.slots[1].x < W / 2 && E.slots[2].x > W / 2, 'the teal guest left of the centre, Lens and the violet guest right');
  assert.ok([0, 1, 2].every((i) => Math.abs(memberTime('enter', 1, i) - (0.6 - 0.2 * i)) < 1e-9), 'each a beat after the last');
  const edge = Math.round((E.slots[2].x + E.slots[3].x) / 2);
  const c = centroids(frame(shot, 0.5, W, H), W, H, 0, H, edge), rest = centroids(frame(shot, 4, W, H), W, H, 0, H, edge);
  const early = centroids(frame(shot, 0.5, W, H), W, H, 0, H, Math.round((E.slots[0].x + E.slots[1].x) / 2), Math.round(W / 2));
  assert.ok(early.teal.n > 200, 'the teal performer, left of centre, plays its rig’s own entrance');
  assert.equal(c.violet.n, 0, 'the violet performer has not stepped in yet');
  assert.ok(rest.violet.n > 200 && Math.abs(rest.violet.x - E.slots[3].x) < 40, 'and later stands on its spot');
});

test('acting frames are deterministic across envs in every format', () => {
  const shots: Shot[] = [
    resolveShot(LINE_UP, 'Payoff', 'x'),
    resolveShot({ mode: 'animation', bubble: 'think', move: 'think', costar: { character: 'lens', move: 'enter', bubble: 'say' } }, 'Beat', 'x'),
    resolveShot({ mode: 'animation', bubble: 'say' }, 'Beat', 'x'),
  ];
  for (const format of FORMAT_IDS) {
    const [W, H] = FORMATS[format];
    for (const [k, shot] of shots.entries()) {
      for (const lt of [0.2, 1.1, 1.35, 2.6]) {
        const once = (e = nodeEnv()) => render(W / 4, H / 4, (ctx) => {
          ctx.scale(0.25, 0.25);
          drawSceneFrame(ctx, 'animation', shot, lt, TIMING, W, H, e);
        }).hash;
        assert.equal(once(), once(env), `${format} shot ${k} @${lt}`);
      }
    }
  }
});

test('a cameo draws its character’s bubble once it has sprung in; footage draws nothing', () => {
  const [W, H] = FORMATS['16:9'], cameo = resolveShot({ mode: 'cameo', bubble: 'exclaim' }, 'Wrap', 'x');
  const ink = (mode: 'cameo' | 'footage', shot: Shot, lt: number) =>
    inkBox(render(W, H, (ctx) => drawSceneFrame(ctx, mode, shot, lt, { firstT: 0, cueT: 0 }, W, H, env)).ctx, W, H).n;
  const without = { ...cameo, bubble: undefined as BubbleId | undefined };
  assert.equal(ink('cameo', cameo, 0.3), ink('cameo', without, 0.3), 'not while springing in');
  assert.ok(ink('cameo', cameo, 1.5) > ink('cameo', without, 1.5) + 100, 'then its "!"');
  assert.equal(ink('footage', cameo, 1.5), 0);
});
