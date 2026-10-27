// Acting: what a performer does beyond its rig's pose, face and move. A bubble over its head (a
// thought, speech, emphasis, a question or two neutral exclamation marks) painted in the rigs' pixel style, and a walk-in from the right
// edge for a performer standing right of the centre whose move is `enter`. Both read only a rig's
// metadata, never its art, and are pure functions of scene-local time, so the preview and the Node export
// draw the same frames.

import type { Performer } from './characters.ts';
import { clamp, ease } from './draw.ts';
import type { BubbleId, Character, Ctx, MotionEnv, RigCanvas } from './types.ts';

export const BUBBLES: readonly BubbleId[] = ['think', 'say', 'exclaim', 'question', 'exclaims'];

/** Where a performer stands at a moment: feet centred on (x, by), `h` tall. */
export interface Spot {
  x: number;
  by: number;
  h: number;
}

/** A rig's width at rest, `h` tall (the default rig's proportions when it declares none). */
export const widthOf = (rig: Character, h: number): number => (rig.aspect ?? 0.62) * h;

/** A rig's whole-pixel scale at height `h`. */
export const pixelOf = (rig: Character, h: number): number => Math.max(1, Math.floor(h / (rig.rows ?? 50)));

/** How far above its feet a rig's head reaches at height `h`, using its declared crown (`crown`). */
export const crownOf = (rig: Character, h: number): number => (rig.crown && rig.rows ? rig.crown * pixelOf(rig, h) : h);

// ---------------------------------------------------------------- bubbles

// Each bubble's body in cells (bubble pixels), the cells between it and the head, and how far it reaches
// back past the point it aims at: the rest leans toward the scene centre.
const BODY: Readonly<Record<BubbleId, { w: number; h: number; gap: number; back: number }>> = {
  think: { w: 26, h: 16, gap: 11, back: 5 },
  say: { w: 22, h: 12, gap: 6, back: 4 },
  exclaim: { w: 5, h: 18, gap: 2, back: 1 },
  question: { w: 13, h: 18, gap: 2, back: 6.5 },
  exclaims: { w: 12, h: 18, gap: 2, back: 6 },
};
// Offset the bubble target toward the scene centre to separate its tail from the body.
const AIM = 0.3;
// The marks centred over the performer's own head instead, just above its crown: `back` is half their width.
const CENTRED: ReadonlySet<BubbleId> = new Set(['question', 'exclaims']);

export interface BubbleLayout {
  /** Screen pixels per cell: the performer's own pixel size, smaller only when the frame has no room. */
  q: number;
  /** The body at rest, in screen pixels, inside the frame. */
  body: { x: number; y: number; w: number; h: number };
  /** Where the tail or the puffs lead: the top of the performer's head, on the side facing the centre. */
  head: readonly [number, number];
  /** 1 when it leans right (the performer is left of the centre or on it), -1 when it leans left. */
  dir: 1 | -1;
}

/**
 * Where a bubble sits over a performer in a W x H frame: above its layout box, aiming at the side of the
 * head facing the scene centre and leaning that way, clamped inside the frame with a small margin. The "?"
 * and the "!!" are centred over the head instead, just above its crown. Its cells are the rig's pixels, or
 * bigger for a small rig so it still reads in the frame, and smaller only when there is no room above the head.
 */
export function bubbleLayout(kind: BubbleId, spot: Spot, rig: Character, W: number, H: number): BubbleLayout {
  const b = BODY[kind], m = Math.round(Math.min(W, H) * 0.02), dir = spot.x > W / 2 ? -1 : 1, centred = CENTRED.has(kind);
  const hx = Math.round(centred ? spot.x : spot.x + dir * AIM * widthOf(rig, spot.h));
  const hy = Math.round(spot.by - (centred ? crownOf(rig, spot.h) : spot.h));
  const size = Math.max(pixelOf(rig, spot.h), Math.floor(Math.min(W, H) / 150));
  const room = Math.min(Math.floor((hy - m) / (b.h + b.gap)), Math.floor((W - 2 * m) / b.w), Math.floor((H - 2 * m) / b.h));
  const q = Math.max(Math.min(2, size), Math.min(size, room)), w = b.w * q, h = b.h * q;
  const x0 = dir > 0 ? hx - b.back * q : hx + b.back * q - w;
  const x = Math.round(clamp(x0, m, W - m - w)), y = Math.round(clamp(hy - (b.gap * q + h), m, H - m - h));
  return { q, body: { x, y, w, h }, head: [hx, hy], dir };
}

/** A bubble at `u` seconds after it appeared: its size, and the state of its marks. */
export interface BubbleBeat {
  /** Size over its size at rest: it pops in past 1 and settles. */
  scale: number;
  /** think: how many of its three dots show, one more every DOT seconds, then round again. */
  dots: number;
  /** say: how many of its five words of talk marks show, typing out and starting over. */
  words: number;
  /** exclaim, question and exclaims (its first mark): the sideways shake just after the pop, -1, 0 or 1. */
  shake: number;
}

const POP = 0.3, DOT = 0.4, DOTS = 1.6, WORD = 0.13, TALK = 1.1, SHAKE = 0.3;
// The "!": a hard overshoot, a dip, a small rebound, then it holds.
const BANG: readonly (readonly [number, number])[] = [[0, 0], [0.1, 1.45], [0.18, 0.85], [0.26, 1.08], [0.32, 1]];

function keyframes(frames: readonly (readonly [number, number])[], u: number): number {
  for (let i = 1; i < frames.length; i++) {
    const [t0, v0] = frames[i - 1], [t1, v1] = frames[i];
    if (u < t1) return v0 + ((v1 - v0) * (u - t0)) / (t1 - t0);
  }
  return frames[frames.length - 1][1];
}

/** The bubble `u` seconds after it appeared; null before. Pure: no clock, no state. */
export function bubbleBeat(kind: BubbleId, u: number): BubbleBeat | null {
  if (u < 0) return null;
  if (kind === 'exclaim' || kind === 'question' || kind === 'exclaims') {
    const end = BANG[BANG.length - 1][0], s = u - end;
    return { scale: keyframes(BANG, u), dots: 0, words: 0, shake: s >= 0 && s < SHAKE ? (Math.floor(s / 0.05) % 2 ? 1 : -1) : 0 };
  }
  return {
    scale: ease.back(clamp(u / POP)),
    dots: kind === 'think' ? 1 + Math.min(2, Math.floor((u % DOTS) / DOT)) : 0,
    words: kind === 'say' ? Math.min(5, 1 + Math.floor((u % TALK) / WORD)) : 0,
    shake: 0,
  };
}

// The house ramps (CHARACTERS.md): the outline, and highlight, base and shade; the "!" uses the default accent, the
// "!!" uses a high-contrast neutral ramp.
const INK: Readonly<Record<string, string>> = { K: '#111214', D: '#2e333b' };
const WHITE = { F: '#eef2f4', L: '#ffffff', S: '#c9d2d8' };
export const BUBBLE_GOLD = { F: '#f5c518', L: '#ffeb8f', S: '#c98f0b' };
// Two equal neutral marks share one clock.
const MARK_W = 5, STEP = 7;

// The bubble canvas, in cells: room for the largest bubble with its tail or puffs.
const CW = 48, CH = 32;

type Inside = (x: number, y: number) => boolean;
const disc = (cx: number, cy: number, r: number): Inside => (x, y) => (x - cx) ** 2 + (y - cy) ** 2 <= r * r;

// The think cloud's lobes in cells, leaning right (mirrored when it leans left): a core, three bumps over
// the top, one each side, three under. Its puffs trail from under it toward the head, a row apart:
// [cells below it, radius, centred on a cell (0.5) or between cells (0)], round with a white middle.
const LOBES: readonly (readonly [number, number, number])[] = [
  [9.5, 8.5, 4.5], [16.5, 8.5, 4.5],
  [7, 5.5, 4.5], [13.5, 4.5, 4.8], [19.5, 6, 4.2], [3.8, 9.5, 3.5], [22.5, 10, 3.2], [7.5, 11.5, 3.8], [13.5, 11.8, 3.6], [19, 11.5, 3.6],
];
const PUFFS: readonly (readonly [number, number, number])[] = [[3.5, 2.5, 0.5], [9, 2, 0]];
const THOUGHT_DOTS = [8, 12, 16];
// Talk marks: two lines of words, [x, length] in cells.
const TALK_ROWS: readonly (readonly (readonly [number, number])[])[] = [[[3, 4], [8, 3], [12, 6]], [[3, 5], [9, 7]]];
const TALK_Y = [4, 7];
// The "?": a hook over a stem and a dot, each stroke 5 cells wide so it keeps 3 of fill inside its outline.
const QUESTION = [
  '...#######...', '.###########.', '#############', '#############', '#####...#####', '#####...#####',
  '........#####', '.......######', '.....#######.', '....######...', '....#####....', '....#####....',
  '.............', '....#####....', '....#####....', '....#####....', '....#####....', '....#####....',
];

function roundRect(w: number, h: number, r: number): Inside {
  return (x, y) => {
    if (x < 0 || y < 0 || x > w || y > h) return false;
    const dx = Math.max(0, Math.abs(x - w / 2) - (w / 2 - r)), dy = Math.max(0, Math.abs(y - h / 2) - (h / 2 - r));
    return dx * dx + dy * dy <= r * r;
  };
}

// A cut-corner box of cells [x0, x0 + w) x [y0, y0 + h).
function chip(x0: number, y0: number, w: number, h: number): Inside {
  return (x, y) => {
    const i = Math.floor(x) - x0, j = Math.floor(y) - y0;
    return i >= 0 && j >= 0 && i < w && j < h && !((i === 0 || i === w - 1) && (j === 0 || j === h - 1));
  };
}

// A tail from (ax, ay) narrowing to the point (tx, ty), `base` cells wide where it leaves the body.
function tail(ax: number, ay: number, tx: number, ty: number, base: number): Inside {
  return (x, y) => {
    if (y < ay || y > ty) return false;
    const t = (y - ay) / Math.max(1, ty - ay), cx = ax + (tx - ax) * t;
    return Math.abs(x - cx) <= (base / 2) * (1 - t) + 0.5;
  };
}

interface Sprite {
  /** Cell colours, row by row, `w` cells a row; empty cells are undefined. */
  cells: (string | undefined)[];
  /** The first column, relative to the body's left edge (the tail or puffs can reach past it). */
  x0: number;
  w: number;
  h: number;
}

// The bubble's shape as cells: an outlined union, filled flat with a highlight along its top left and a
// shade along its bottom, then its marks.
function sprite(kind: BubbleId, lay: BubbleLayout, beat: BubbleBeat): Sprite {
  const b = BODY[kind], { q, body, dir } = lay;
  const hc = [(lay.head[0] - body.x) / q, (lay.head[1] - body.y) / q] as const;
  const shapes: Inside[] = [];
  const ax = clamp(hc[0] + dir * 3, 3, b.w - 3);
  const dy = hc[1] - b.h;
  if (kind === 'think') {
    for (const [x, y, r] of LOBES) shapes.push(disc(dir > 0 ? x : b.w - x, y, r));
    for (const [d, r, c] of PUFFS) if (dy >= d + r) shapes.push(disc(Math.round(ax + ((hc[0] - ax) * d) / dy) + c, b.h + d, r));
  } else if (kind === 'say') {
    shapes.push(roundRect(b.w, b.h, 3));
    if (dy >= 2) shapes.push(tail(ax, b.h - 1, ax + (hc[0] - ax) * 0.85, b.h + dy * 0.85, 6));
  } else if (kind === 'question') {
    shapes.push((x, y) => QUESTION[Math.floor(y)]?.[Math.floor(x)] === '#');
  } else {
    for (let k = 0; k < (kind === 'exclaims' ? 2 : 1); k++) shapes.push(chip(k * STEP, 0, MARK_W, 12), chip(k * STEP, 13, MARK_W, 5));
  }
  const x0 = Math.min(0, Math.floor(hc[0]) - 3), w = Math.min(CW, Math.max(b.w, Math.ceil(hc[0]) + 3) - x0);
  const h = Math.min(CH, Math.max(b.h, Math.ceil(hc[1])));
  // The shape, with a one-cell empty border so every cell has four neighbours.
  const mw = w + 2, mask = new Uint8Array(mw * (h + 2));
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    if (shapes.some((s) => s(x + x0 + 0.5, y + 0.5))) mask[(y + 1) * mw + x + 1] = 1;
  }
  const inside = (x: number, y: number): boolean => mask[(y + 1) * mw + x + 1] === 1;
  const edge = (x: number, y: number): boolean =>
    inside(x, y) && !(inside(x - 1, y) && inside(x + 1, y) && inside(x, y - 1) && inside(x, y + 1));
  const fill = kind === 'exclaim' ? BUBBLE_GOLD : WHITE, cells: (string | undefined)[] = new Array(w * h);
  // Shade both neutral marks identically.
  const pair = kind === 'exclaims', wide = pair ? MARK_W : b.w;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    if (!inside(x, y)) continue;
    const across = pair ? (x + x0) % STEP : x + x0, ramp = pair ? WHITE : fill;
    const lit = (edge(x, y - 1) && across < wide * 0.6) || (edge(x - 1, y) && y < b.h * 0.55);
    cells[y * w + x] = edge(x, y) ? INK.K : lit ? ramp.L : edge(x, y + 1) ? ramp.S : ramp.F;
  }
  const mark = (x: number, y: number, n: number): void => {
    for (let i = 0; i < n; i++) cells[y * w + x - x0 + i] = INK.D;
  };
  THOUGHT_DOTS.slice(0, beat.dots).forEach((x) => { mark(x, 8, 2); mark(x, 9, 2); });
  TALK_ROWS.flat().slice(0, beat.words).forEach(([x, n], k) => mark(x, TALK_Y[k < TALK_ROWS[0].length ? 0 : 1], n));
  return { cells, x0, w, h };
}

// One bubble canvas per env, cleared on every paint so reuse stays pure.
const sheets = new WeakMap<MotionEnv, RigCanvas>();

function sheet(env: MotionEnv): { canvas: RigCanvas; oc: Ctx } {
  let canvas = sheets.get(env);
  if (!canvas) {
    canvas = env.createCanvas(CW, CH);
    sheets.set(env, canvas);
  }
  const oc = canvas.getContext('2d');
  if (!oc) throw new Error('motion-core: env.createCanvas returned a canvas without a 2d context');
  return { canvas, oc };
}

// A sprite painted into the env's bubble canvas.
function painted(env: MotionEnv, { cells, w }: Sprite): RigCanvas {
  const { canvas, oc } = sheet(env);
  oc.clearRect(0, 0, CW, CH);
  cells.forEach((colour, i) => {
    if (!colour) return;
    oc.fillStyle = colour;
    oc.fillRect(i % w, Math.floor(i / w), 1, 1);
  });
  return canvas;
}

/**
 * Draws a performer's bubble `u` seconds after it appeared (nothing before): painted cell by cell into a
 * small canvas and drawn at a whole-pixel scale with smoothing off, like the rigs, so it stays crisp. It
 * grows out of the top of the head as it pops in; each mark of the "!!" out of the head under it.
 */
export function drawBubble(ctx: Ctx, kind: BubbleId, spot: Spot, rig: Character, u: number, W: number, H: number, env: MotionEnv): void {
  const beat = bubbleBeat(kind, u);
  if (!beat) return;
  const lay = bubbleLayout(kind, spot, rig, W, H);
  if (kind === 'exclaims') {
    drawPair(ctx, lay, u, env);
    return;
  }
  const p = Math.round(lay.q * beat.scale);
  if (p < 1) return;
  const spr = sprite(kind, lay, beat), { x0, w, h } = spr, canvas = painted(env, spr);
  const [hx, hy] = lay.head, hcx = (hx - lay.body.x) / lay.q, hcy = (hy - lay.body.y) / lay.q;
  const ox = Math.round(hx - (hcx - x0) * p + beat.shake * Math.max(1, Math.round(lay.q / 2)));
  const oy = Math.round(hy - hcy * p);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(canvas, 0, 0, w, h, ox, oy, w * p, h * p);
  ctx.restore();
}

// Both marks pop and shake together, grown out of the head under their middles.
function drawPair(ctx: Ctx, lay: BubbleLayout, u: number, env: MotionEnv): void {
  const spr = sprite('exclaims', lay, { scale: 1, dots: 0, words: 0, shake: 0 }), canvas = painted(env, spr);
  const hy = lay.head[1], hcy = (hy - lay.body.y) / lay.q;
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  for (let k = 0; k < 2; k++) {
    const beat = bubbleBeat('exclaim', u), p = beat ? Math.round(lay.q * beat.scale) : 0;
    if (!beat || p < 1) continue;
    const mid = lay.body.x + (k * STEP + MARK_W / 2) * lay.q;
    const ox = Math.round(mid - (MARK_W / 2) * p + beat.shake * Math.max(1, Math.round(lay.q / 2)));
    ctx.drawImage(canvas, k * STEP - spr.x0, 0, MARK_W, spr.h, ox, Math.round(hy - hcy * p), MARK_W * p, spr.h * p);
  }
  ctx.restore();
}

// ---------------------------------------------------------------- entrances from the right

// Heights per second a performer walks in at, and the seconds it takes to stop on its spot.
const WALK = 1.5, STOP = 0.3;

// How far a performer walks in from off the right edge, and how fast.
function walkIn(rig: Character, spot: Spot, W: number): { dist: number; v: number } {
  return { dist: W - spot.x + widthOf(rig, spot.h) / 2 + spot.h * 0.1, v: WALK * spot.h };
}

/** Seconds a performer walking in from the right (`enterFromRight`) takes to stop on its spot, from its clock's 0. */
export function entranceS(rig: Character, spot: Spot, W: number): number {
  const { dist, v } = walkIn(rig, spot, W);
  return dist / v + STOP / 2;
}

/** Where a performer is drawn at a moment: its x, which way it faces, and what its rig plays. */
export interface Entrance {
  x: number;
  face: 1 | -1;
  performer: Performer;
  /** Seconds since it arrived on its spot (negative while walking in); Infinity when it did not walk in. */
  since: number;
}

const moveOf = (rig: Character, id: string): string => (rig.moves.includes(id) ? id : rig.moves[0]);

/**
 * A performer standing right of the centre whose move is `enter` walks in from off the right edge,
 * facing left, at a steady pace (its rig's `walk`) and stops on its spot, then idles there, still facing
 * left. Anyone else (the lead on the left, whose rig plays its own entrance) is returned as is.
 */
export function enterFromRight(p: Performer, rig: Character, spot: Spot, face: 1 | -1, W: number): Entrance {
  if (p.move !== 'enter' || spot.x <= W / 2) return { x: spot.x, face, performer: p, since: Infinity };
  const t = p.t ?? 0, { dist, v } = walkIn(rig, spot, W), T = entranceS(rig, spot, W);
  const dx = t <= 0 ? dist : t < T - STOP ? dist - v * t : t < T ? (v * (T - t) ** 2) / (2 * STOP) : 0;
  return { x: spot.x + dx, face: -1, performer: { ...p, move: moveOf(rig, t < T ? 'walk' : 'idle') }, since: t - T };
}
