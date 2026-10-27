// Ensemble shots: up to three more performers after the lead (and the co-star), stood in one row.
// The row generalises the duo's layout: each rig is sized by its declared `scale` so every rig keeps the
// same pixel size, they all shrink together to fit the frame, feet level, with the headline and a
// reduced diagram above. Each member has its own clock; an ensemble is a line-up, so a duo's interaction
// is not played in one.

import { drawBubble, enterFromRight, entranceS, widthOf } from './acting.ts';
import { characterOf, performer, type Performer } from './characters.ts';
import { fontOf } from './draw.ts';
import { costarPerformer, costarTime, duoBase, overCostar, perform, type DuoSlot } from './duo.ts';
import { drawStageBoard, isBoard } from './ideas.ts';
import { panAt, panDistance, panExtras, panShift } from './pan.ts';
import { backdrop, diagram, headline, headlineFoot } from './scene.ts';
import type { BubbleId, Character, Costar, Ctx, Layout, MotionEnv, Shot, ShotTiming } from './types.ts';

export const ENSEMBLE_MAX = 3;

/** An ensemble's layout: the duo's headline and diagram, and each performer's slot, left to right. */
export interface EnsembleLayout extends Layout {
  slots: DuoSlot[];
}

/**
 * Where a row of performers stands in a W x H frame, lead first. The tallest rig (by declared `scale`)
 * gets the format's height and the others their share of it; when their widths (`aspect`) and the gaps
 * between them would not fit inside the frame, all shrink together. Spare room widens the gaps, up to a
 * limit, and the row is centred.
 */
export function ensembleLayout(W: number, H: number, rigs: readonly Character[]): EnsembleLayout {
  const { L, by, h } = duoBase(W, H), n = rigs.length;
  const top = Math.max(...rigs.map((r) => r.scale ?? 1)), hs = rigs.map((r) => (h * (r.scale ?? 1)) / top);
  const ws = rigs.map((r, i) => widthOf(r, hs[i])), sum = ws.reduce((a, b) => a + b, 0);
  const room = W * 0.94, k = Math.min(1, (room - (n - 1) * W * 0.015) / sum);
  const gap = n > 1 ? Math.min(W * 0.06, (room - k * sum) / (n - 1)) : 0;
  let left = (W - k * sum - (n - 1) * gap) / 2;
  const slots = ws.map((w, i): DuoSlot => {
    const slot: DuoSlot = { x: left + (k * w) / 2, by, h: hs[i] * k, face: 1 };
    left += k * w + gap;
    return slot;
  });
  return { ...L, characterX: slots[0].x, characterY: by, characterH: slots[0].h, slots };
}

// Each member on its own clock, like the co-star: out of phase with the rest, and entering later still.
const PHASES = [0.61, 2.19, 2.83], ENTER_AFTER = 0.2;

export const memberTime = (move: string, lt: number, i: number): number =>
  move === 'enter' ? lt - ENTER_AFTER * (i + 2) : lt + PHASES[i % PHASES.length];

export function memberPerformer(member: Costar, lt: number, i: number): Performer {
  const { character, palette, pose, expression, move, custom } = member;
  return { character, palette, pose, expression, move, t: memberTime(move, lt, i), custom };
}

/** The ensemble members' places in the row: after the lead and the co-star. */
export const memberSlots = (shot: Pick<Shot, 'costar' | 'ensemble'>): number[] =>
  (shot.ensemble ?? []).slice(0, ENSEMBLE_MAX).map((_, i) => i + (shot.costar ? 2 : 1));

/**
 * An ensemble shot at scene-local time `lt`: backdrop, the reduced diagram, the row drawn right to left
 * (so the lead is in front), everyone's bubbles, and the headline on top. `shot.ensemble` must be set; a
 * co-star stands second. Anyone right of the centre whose move is `enter` walks in from the right edge.
 */
export function drawEnsembleShot(
  ctx: Ctx, shot: Shot, lt: number, timing: ShotTiming, W: number, H: number, env: MotionEnv,
): void {
  const row: { p: Performer; bubble?: BubbleId }[] = [
    { p: performer(shot, lt), bubble: shot.bubble },
    ...(shot.costar ? [{ p: costarPerformer(shot.costar, lt), bubble: shot.costar.bubble }] : []),
    ...(shot.ensemble ?? []).slice(0, ENSEMBLE_MAX).map((m, i) => ({ p: memberPerformer(m, lt, i), bubble: m.bubble })),
  ];
  const rigs = row.map(({ p }) => characterOf(p.character)), S = ensembleLayout(W, H, rigs), cue = timing.cueT ?? 1;
  // A pan slides the world, and everyone standing in it, in from the left. Anyone walking in from the right
  // does so on a clock that lands it as the pan settles, plus however much later than a co-star it would.
  const pan = panAt(shot.camera, lt, timing.endT), shift = panShift(pan, pan ? panDistance(S.slots[0].x, S.slots[0].h, rigs[0], W) : 0);
  const staged = row.map(({ p }, i) => enterFromRight(pan && p.move === 'enter' && S.slots[i].x > W / 2
    ? { ...p, t: (p.t ?? 0) - costarTime('enter', lt) + lt - pan.dur + entranceS(rigs[i], S.slots[i], W) } : p, rigs[i], S.slots[i], S.slots[i].face, W));
  const xs = staged.map((s) => s.x + (s.since === Infinity ? shift : 0));
  ctx.save();
  ctx.textBaseline = 'alphabetic';
  backdrop(ctx, shot.backdrop, W, H, lt, shift);
  if (pan) panExtras(ctx, W, H, S.slots[0].by, shift, pan.alpha);
  if (isBoard(shot.diagram)) {
    drawStageBoard(ctx, shot.diagram, lt, timing, W, H, {
      spots: S.slots.map((s, i) => ({ ...s, rig: rigs[i] })), bubbles: row.map((r) => r.bubble),
      over: memberSlots(shot),
      top: overCostar(shot) ? 0 : headlineFoot(ctx, shot, S, env), shift, labels: shot.boardLabels,
    }, fontOf(env));
  } else diagram(ctx, shot.diagram, lt, cue, S, fontOf(env));
  for (let i = row.length - 1; i >= 0; i--) perform(ctx, { ...S.slots[i], face: staged[i].face }, xs[i], 0, 0, staged[i].performer, env);
  for (let i = row.length - 1; i >= 0; i--) {
    const kind = row[i].bubble;
    if (kind) drawBubble(ctx, kind, { ...S.slots[i], x: xs[i] }, rigs[i], Math.min(lt - cue, staged[i].since), W, H, env);
  }
  if (!overCostar(shot)) headline(ctx, shot, lt, timing.firstT ?? 0.4, S, env);
  ctx.restore();
}
