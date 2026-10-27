// Duo shots: two characters in one animated scene. The lead stands left, the co-star right, each
// sized by its rig's declared scale; when they interact they face each other. An interaction is a pure
// function of scene-local time around the shot's beat (`timing.cueT`, the cue word), so the preview and
// the Node export draw the same frames.

import { drawBubble, enterFromRight, entranceS } from './acting.ts';
import { characterOf, drawCharacter, drawMissingCharacter, performer, type Performer } from './characters.ts';
import { clamp, ease, fontOf } from './draw.ts';
import { boardRect, drawStageBoard, isBoard, type StageBoard } from './ideas.ts';
import { panAt, panDistance, panExtras, panShift } from './pan.ts';
import { BRIEF_TALL } from './brief.ts';
import { backdrop, diagram, diagramRow, headline, headlineFoot, layoutFor } from './scene.ts';
import type { Character, Costar, Ctx, Interaction, Layout, MotionEnv, Shot, ShotTiming } from './types.ts';

export const INTERACTIONS: readonly Interaction[] = ['none', 'face', 'fistbump', 'highfive', 'point'];

// Fallback proportions for a rig that declares none (see Character.aspect and .hands).
const SPARK_GOLD = '#f5c518', SPARK_LIGHT = '#ffe372';
const ASPECT = 0.62, REACH = 0.35, ROWS = 50;

/** One character's spot in a duo: feet centred on (x, by), `h` tall, facing right (1) or mirrored (-1). */
export interface DuoSlot {
  x: number;
  by: number;
  h: number;
  face: 1 | -1;
}

/** A duo's layout: the headline and a reduced diagram centred above the pair, and each character's slot. */
export interface DuoLayout extends Layout {
  lead: DuoSlot;
  costar: DuoSlot;
}

// Per format: the pair's centres, where their feet stand, the tallest one's height, and the text and
// diagram boxes. The diagram is kept but reduced, centred between the headline and the heads, so the
// Diagram pick still shows and `point` has something to point at; the pair and their meeting point own
// the lower frame. Feet stay above the caption line.
export function duoBase(W: number, H: number): { L: Layout; xs: readonly [number, number]; by: number; h: number } {
  const L = layoutFor(W, H), text = { left: false, center: true };
  if (W > H) {
    return { L: { ...L, ...text, tx: W * 0.19, ty: H * 0.12, tw: W * 0.62, fs: 72, dx: W * 0.36, dy: H * 0.3, dw: W * 0.28 },
      xs: [W / 3, (W * 2) / 3], by: H * 0.86, h: H * 0.46 };
  }
  if (W < H) {
    return { L: { ...L, ...text, tx: W * 0.08, ty: H * 0.1, tw: W * 0.84, fs: 88, dx: W * 0.08, dy: H * 0.33, dw: W * 0.84 },
      xs: [W * 0.27, W * 0.73], by: H * 0.8, h: H * 0.32 };
  }
  return { L: { ...L, ...text, tx: W * 0.08, ty: H * 0.1, tw: W * 0.84, fs: 64, dx: W * 0.25, dy: H * 0.27, dw: W * 0.5 },
    xs: [W * 0.27, W * 0.73], by: H * 0.84, h: H * 0.45 };
}

/**
 * Where a duo stands in a W x H frame. The taller rig (by its declared `scale`) gets the format's
 * height and the other its share of it; both shrink together if their widths would not fit side by
 * side inside the frame. With `facing` the co-star is mirrored so the two face each other.
 */
export function duoLayout(W: number, H: number, lead: Character, costar: Character, facing: boolean): DuoLayout {
  const { L, xs: [xl, xc], by, h } = duoBase(W, H);
  const sl = lead.scale ?? 1, sc = costar.scale ?? 1, top = Math.max(sl, sc);
  const hl = (h * sl) / top, hc = (h * sc) / top;
  const wl = ((lead.aspect ?? ASPECT) * hl) / 2, wc = ((costar.aspect ?? ASPECT) * hc) / 2;
  const edge = W * 0.03;
  const k = Math.min(1, (xc - xl - W * 0.02) / (wl + wc), (xl - edge) / wl, (W - edge - xc) / wc);
  return {
    ...L, characterX: xl, characterY: by, characterH: hl * k,
    lead: { x: xl, by, h: hl * k, face: 1 },
    costar: { x: xc, by, h: hc * k, face: facing ? -1 : 1 },
  };
}

/**
 * One character's part in an interaction at a moment: `approach` is how far it has leaned toward the
 * other (1 = hands touching, negative = pulled away), `squash` the impact squash (0..1), and `posed`
 * whether it holds the interaction's pose (its own move pauses meanwhile).
 */
export interface Lean {
  approach: number;
  squash: number;
  posed: boolean;
}

/** Both characters' parts at a moment, plus the impact burst's and the pointer's progress (0..1) when showing. */
export interface DuoBeat {
  lead: Lean;
  costar: Lean;
  burst: number | null;
  pointer: { p: number; alpha: number; blink: boolean } | null;
}

// Seconds around the beat. Wind-up, then an accelerating lunge that touches exactly on the beat, a
// short impact hold, a recoil past rest (follow-through) and a settle.
const WIND_UP = 0.6, LUNGE = 0.3, IMPACT = 0.07, RECOIL = 0.32, SETTLE = 0.8, BURST = 0.4;
const PULL_BACK = 0.15, OVERSHOOT = 0.2;
// `point`: the co-star raises its arm just before the beat and steps in; the pointer draws, holds, fades.
const POINT_IN = 0.3, STEP = 0.3, POINTER_DRAW = 0.45, POINTER_HOLD = 2.6, POINTER_FADE = 0.4;

const REST: Lean = { approach: 0, squash: 0, posed: false };

function lunge(u: number): number {
  if (u < -WIND_UP || u >= SETTLE) return 0;
  if (u < -LUNGE) return -PULL_BACK * ease.out((u + WIND_UP) / (WIND_UP - LUNGE));
  if (u < 0) {
    const p = (u + LUNGE) / LUNGE;
    return -PULL_BACK + (1 + PULL_BACK) * p * p;
  }
  if (u < IMPACT) return 1;
  if (u < RECOIL) return 1 - (1 + OVERSHOOT) * ease.out((u - IMPACT) / (RECOIL - IMPACT));
  return -OVERSHOOT * (1 - ease.out((u - RECOIL) / (SETTLE - RECOIL)));
}

function step(u: number): number {
  if (u < -POINT_IN) return 0;
  if (u < -0.1) return -0.08 * ease.out((u + POINT_IN) / (POINT_IN - 0.1));
  if (u < 0.25) return -0.08 + (STEP + 0.08) * ease.back((u + 0.1) / 0.35);
  return STEP;
}

/** The interaction at scene-local time `lt` for a shot whose beat is `cueT`. Pure: no clock, no state. */
export function duoBeat(kind: Interaction, lt: number, cueT: number): DuoBeat {
  const u = lt - cueT;
  if (kind === 'fistbump' || kind === 'highfive') {
    const lean: Lean = { approach: lunge(u), squash: u >= 0 && u < IMPACT ? 1 - u / IMPACT : 0, posed: u >= -WIND_UP && u < SETTLE };
    return { lead: lean, costar: lean, burst: u >= 0 && u < BURST ? u / BURST : null, pointer: null };
  }
  if (kind === 'point') {
    const showing = u >= 0 && u < POINTER_HOLD + POINTER_FADE;
    return {
      lead: REST,
      costar: { approach: step(u), squash: 0, posed: u >= -POINT_IN },
      burst: null,
      pointer: showing ? { p: ease.out(clamp(u / POINTER_DRAW)), alpha: 1 - clamp((u - POINTER_HOLD) / POINTER_FADE), blink: Math.floor(u * 4) % 2 === 1 } : null,
    };
  }
  return { lead: REST, costar: REST, burst: null, pointer: null };
}

// What each interaction asks of a rig that has it: the pose held, and, for a rig without hand anchors,
// how high that pose's hand is (of its height above the feet), from the default rig's.
const POSE: Partial<Record<Interaction, string>> = { fistbump: 'point', highfive: 'wave', point: 'point' };
const HAND: Partial<Record<Interaction, number>> = { fistbump: 0.31, highfive: 0.45, point: 0.31 };
const SQUASH = 0.08;

/** A rig's front hand in an interaction's pose, `h` tall: [forward, up] screen pixels from its feet. */
/** The rig's whole-pixel scale at height `h`; the default rig's rows for a rig that declares none. */
function pixelOf(rig: Character, h: number): number {
  return Math.max(1, Math.floor(h / (rig.rows ?? ROWS)));
}

function handOf(rig: Character, kind: Interaction, h: number): readonly [number, number] {
  const at = rig.hands?.[POSE[kind] ?? ''];
  if (!at || !rig.rows) return [REACH * h, (HAND[kind] ?? 0) * h];
  const g = pixelOf(rig, h);
  return [at[0] * g, at[1] * g];
}

// The co-star runs on its own clock so the pair never moves in lockstep: its idle bob, blink and loops
// are out of phase with the lead's, and it enters a beat later.
const COSTAR_PHASE = 1.37, COSTAR_ENTER = 0.2;

export const costarTime = (move: string, lt: number): number => (move === 'enter' ? lt - COSTAR_ENTER : lt + COSTAR_PHASE);

export function costarPerformer(costar: Costar, lt: number): Performer {
  const { character, palette, pose, expression, move, custom } = costar;
  return { character, palette, pose, expression, move, t: costarTime(move, lt), custom };
}

/** Whether a stage shot's headline goes over its co-star (staging.ts draws it there) instead of its own place. */
export const overCostar = (shot: Pick<Shot, 'headlineAt' | 'costar'>): boolean => shot.headlineAt === 'costar' && !!shot.costar;

function act(p: Performer, rig: Character, kind: Interaction, lean: Lean): Performer {
  if (!lean.posed) return p;
  const pose = POSE[kind];
  return { ...p, pose: pose && rig.poses.includes(pose) ? pose : p.pose, move: rig.moves.includes('idle') ? 'idle' : rig.moves[0] };
}

// A character at x on its slot, `lift` pixels off the ground, mirrored when it faces left and squashed
// about its feet by k on impact. The origin sits on whole pixels, so a mirrored pixel rig stays crisp.
export function perform(ctx: Ctx, slot: DuoSlot, x: number, lift: number, k: number, p: Performer, env: MotionEnv): void {
  ctx.save();
  ctx.translate(Math.round(x), Math.round(slot.by));
  ctx.scale(slot.face * (1 + k), 1 - k);
  drawCharacter(ctx, 0, 0, slot.h, lift ? { ...p, lift } : p, env, false);
  ctx.restore();
  drawMissingCharacter(ctx, Math.round(x), Math.round(slot.by), slot.h, p.character, env);
}

function dot(ctx: Ctx, x: number, y: number, q: number, color: string): void {
  ctx.fillStyle = color;
  ctx.fillRect(Math.round(x - q / 2), Math.round(y - q / 2), q, q);
}

const FLASH: readonly (readonly [number, number])[] = [[0, 0], [1, 0], [-1, 0], [2, 0], [-2, 0], [0, 1], [0, -1], [0, 2], [0, -2]];

// The impact mark, in the rigs' own pixel size q: a white flash, then eight rays bursting out and fading.
function burst(ctx: Ctx, x: number, y: number, q: number, p: number): void {
  ctx.save();
  ctx.globalAlpha = 1 - p * p;
  if (p < 0.3) for (const [i, j] of FLASH) dot(ctx, x + i * q, y + j * q, q, '#ffffff');
  const r = (3 + 9 * ease.out(p)) * q;
  for (let k = 0; k < 8; k++) {
    const a = (k * Math.PI) / 4;
    for (let j = 0; j < (k % 2 ? 2 : 3); j++) {
      dot(ctx, x + Math.cos(a) * (r + j * q), y + Math.sin(a) * (r + j * q), q, j ? SPARK_GOLD : SPARK_LIGHT);
    }
  }
  ctx.restore();
}

// A dotted pixel trail from the fingertip F curving up to the target T, drawn to progress p, with a
// twinkle at T once it arrives.
function pointer(ctx: Ctx, F: readonly [number, number], T: readonly [number, number], q: number, pt: NonNullable<DuoBeat['pointer']>): void {
  const C = [T[0] + (F[0] - T[0]) * 0.35, F[1]];
  const n = Math.max(2, Math.round((Math.hypot(C[0] - F[0], C[1] - F[1]) + Math.hypot(T[0] - C[0], T[1] - C[1])) / (3 * q)));
  ctx.save();
  ctx.globalAlpha = pt.alpha;
  for (let i = 1; i <= n && i / n <= pt.p; i++) {
    const t = i / n, a = (1 - t) * (1 - t), b = 2 * (1 - t) * t, c = t * t;
    dot(ctx, a * F[0] + b * C[0] + c * T[0], a * F[1] + b * C[1] + c * T[1], q, SPARK_GOLD);
  }
  if (pt.p >= 1) {
    const r = (pt.blink ? 3 : 2) * q;
    for (const [i, j] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) dot(ctx, T[0] + i * r, T[1] + j * r, q, '#ffe372');
  }
  ctx.restore();
}

/**
 * A duo shot at scene-local time `lt`: backdrop, the reduced diagram, the co-star then the lead, the
 * interaction's mark, their bubbles, and the headline centred on top. `shot.costar` must be set. A fist
 * bump or high five meets at the two rigs' hand anchors: they lean in until the fronts of their hands
 * touch, and the one whose hand is lower rises off the ground to meet the other (a smaller performer hops up to a
 * taller co-star). A co-star whose move is `enter` walks in from the right edge (acting.ts).
 */
export function drawDuoShot(
  ctx: Ctx, shot: Shot, lt: number, timing: ShotTiming, W: number, H: number, env: MotionEnv,
): void {
  const costar = shot.costar!, kind = shot.interaction ?? 'none';
  const lr = characterOf(shot.character), cr = characterOf(costar.character);
  const D = duoLayout(W, H, lr, cr, kind !== 'none');
  const cue = timing.cueT ?? 1, beat = duoBeat(kind, lt, cue);
  const [fl, ul] = handOf(lr, kind, D.lead.h), [fc, uc] = handOf(cr, kind, D.costar.h);
  const lean = Math.max(0, (D.costar.x - D.lead.x - fl - fc) / 2);
  const kl = SQUASH * beat.lead.squash, kc = SQUASH * beat.costar.squash;
  // A pan slides the world, the lead with it, in from the left; a co-star walking in arrives as it settles.
  const pan = panAt(shot.camera, lt, timing.endT), shift = panShift(pan, pan ? panDistance(D.lead.x, D.lead.h, lr, W) : 0);
  const cp = costarPerformer(costar, lt);
  const cin = enterFromRight(pan && cp.move === 'enter' ? { ...cp, t: lt - pan.dur + entranceS(cr, D.costar, W) } : cp, cr, D.costar, D.costar.face, W);
  const cshift = cin.since === Infinity ? shift : 0;
  // The fronts of the two hands; on impact they hold there and each body squashes out behind its hand.
  const hl = D.lead.x + shift + lean * beat.lead.approach + fl, hc = D.costar.x + cshift - lean * beat.costar.approach - fc;
  const xl = hl - fl * (1 + kl), xc = hc + fc * (1 + kc) + cin.x - D.costar.x;
  const touch = kind === 'fistbump' || kind === 'highfive';
  const ll = touch ? Math.round(Math.max(0, uc - ul) * clamp(beat.lead.approach)) : 0;
  const lc = touch ? Math.round(Math.max(0, ul - uc) * clamp(beat.costar.approach)) : 0;
  const q = Math.max(2, Math.min(pixelOf(lr, D.lead.h), pixelOf(cr, D.costar.h)));
  ctx.save();
  ctx.textBaseline = 'alphabetic';
  const board: StageBoard | null = isBoard(shot.diagram) ? {
    spots: [{ ...D.lead, rig: lr }, { ...D.costar, rig: cr }], bubbles: [shot.bubble, costar.bubble],
    over: [], top: overCostar(shot) ? 0 : headlineFoot(ctx, shot, D, env), shift,
    labels: shot.boardLabels,
  } : null;
  backdrop(ctx, shot.backdrop, W, H, lt, shift);
  if (pan) panExtras(ctx, W, H, D.lead.by, shift, pan.alpha);
  if (board && isBoard(shot.diagram)) drawStageBoard(ctx, shot.diagram, lt, timing, W, H, board, fontOf(env));
  else diagram(ctx, shot.diagram, lt, cue, D, fontOf(env));
  perform(ctx, { ...D.costar, face: cin.face }, xc, lc, kc, act(cin.performer, cr, kind, beat.costar), env);
  perform(ctx, D.lead, xl, ll, kl, act(performer(shot, lt), lr, kind, beat.lead), env);
  if (beat.burst !== null) burst(ctx, (hl + hc) / 2, (D.lead.by - ll - ul + D.costar.by - lc - uc) / 2, q, beat.burst);
  if (beat.pointer) {
    const row = diagramRow(D), bottom = row.Y + (shot.diagram === 'brief' ? row.bw * BRIEF_TALL : row.bh);
    const R = board && isBoard(shot.diagram) ? boardRect(W, H, board) : null;
    pointer(ctx, [xc + cin.face * fc, D.costar.by - uc], R ? [R.x + shift + R.w / 2, R.y + R.h + 2 * q] : [row.X + row.total / 2, bottom + 2 * q], q, beat.pointer);
  }
  if (costar.bubble) drawBubble(ctx, costar.bubble, { ...D.costar, x: xc }, cr, Math.min(lt - cue, cin.since), W, H, env);
  if (shot.bubble) drawBubble(ctx, shot.bubble, { ...D.lead, x: xl }, lr, lt - cue, W, H, env);
  if (!overCostar(shot)) headline(ctx, shot, lt, timing.firstT ?? 0.4, D, env);
  ctx.restore();
}
