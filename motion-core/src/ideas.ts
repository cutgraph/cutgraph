// Independent diagram presets, fitted around the current performers and acting marks.
// Geometry and reveal timing belong to each preset; no scene carries state into another.
import { bubbleLayout, crownOf, widthOf, type Spot } from './acting.ts';
import { clamp, ease, paintText, prog, rr, setFont } from './draw.ts';
import type { BoardKind, BubbleId, Character, Ctx, ShotTiming } from './types.ts';

export const BOARD_KINDS: readonly BoardKind[] = ['grid', 'chain', 'radial', 'tree', 'list'];
export const isBoard = (kind: string): kind is BoardKind => (BOARD_KINDS as readonly string[]).includes(kind);
export const BOARD_W = 3.8, BOARD_H = 1.8;
export const BOARD_LABELS_MAX = 6, BOARD_LABEL_MAX = 12;
export const BOX_W = 0.82, BOX_H = 0.24;
const NAME_FILL = 0.84, LINE = 0.02;
type Pt = readonly [number, number];
type Link = readonly [number, number];
interface Preset { points: Pt[]; edges: Link[]; starts: number[]; duration: number }

/** Build geometry from the content count rather than reserving a fixed set of slots. */
function presetOf(kind: BoardKind, count: number): Preset {
  const n = Math.round(clamp(Number.isFinite(count) ? count : 3, 1, BOARD_LABELS_MAX));
  const cols = Math.min(3, n), rows = Math.ceil(n / cols);
  const points: Pt[] = [], edges: Link[] = [], starts: number[] = [];
  for (let i = 0; i < n; i++) {
    const row = Math.floor(i / cols), col = i % cols;
    const level = Math.floor(Math.log2(i + 1)), first = 2 ** level - 1;
    const onLevel = Math.min(2 ** level, n - first);
    const angle = -Math.PI / 2 + (i - 1) * Math.PI * 2 / Math.max(1, n - 1);
    if (kind === 'list') points.push([BOARD_W / 2, (i + 0.5) * BOARD_H / n]);
    else if (kind === 'tree') points.push([(i - first + 1) * BOARD_W / (onLevel + 1), (level + 0.5) * BOARD_H / (Math.floor(Math.log2(n)) + 1)]);
    else if (kind === 'radial') points.push(i === 0 ? [BOARD_W / 2, BOARD_H / 2] : [BOARD_W / 2 + 1.3 * Math.cos(angle), BOARD_H / 2 + 0.65 * Math.sin(angle)]);
    else {
      const inRow = Math.min(cols, n - row * cols);
      const x = kind === 'chain' && row % 2 ? inRow - col : col + 1;
      points.push([x * BOARD_W / (inRow + 1), (row + 0.5) * BOARD_H / rows]);
    }
    if (i && kind === 'chain') edges.push([i - 1, i]);
    if (i && kind === 'radial') edges.push([0, i]);
    if (i && kind === 'tree') edges.push([Math.floor((i - 1) / 2), i]);
    starts.push(kind === 'grid' ? row * 0.18 : kind === 'tree' ? level * 0.16 : kind === 'radial' ? (i ? 0.22 : 0) : i * 0.1);
  }
  return { points, edges, starts, duration: kind === 'radial' ? 0.5 : 0.3 };
}
export interface BoxState { x: number; y: number; scale: number; alpha: number }
export interface BoardState { boxes: BoxState[]; edges: readonly Link[]; links: number[] }

/** Deterministic scene-local reveals, compressed to finish even in a short scene. */
export function boardState(kind: BoardKind, lt: number, timing: ShotTiming, count = 3): BoardState {
  const preset = presetOf(kind, count), span = Math.max(...preset.starts) + preset.duration;
  const end = timing.endT === undefined ? undefined : Math.max(0, timing.endT);
  const cue = clamp(timing.cueT ?? 0, 0, end === undefined ? Infinity : end * 0.5);
  const pace = end === undefined ? 1 : Math.max(0.000001, Math.min(1, (end - cue) / span));
  const boxes = preset.points.map(([x, y], i) => {
    const alpha = ease.out(prog(lt, cue + preset.starts[i] * pace, preset.duration * pace));
    return { x, y, alpha, scale: 0.9 + 0.1 * alpha };
  });
  return { boxes, edges: preset.edges, links: preset.edges.map(([a, b]) => Math.min(boxes[a].alpha, boxes[b].alpha)) };
}

/** A performer on the stage: feet centred on (x, by), `h` tall, its rig. */
export interface BoardSpot extends Spot {
  rig: Character;
}

/** The diagram region in the frame. */
export interface BoardRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A box in the frame. */
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const ASPECT = BOARD_W / BOARD_H;

/** Where the board may go: [x0, x1] x [y0, y1], centred on cx, at the bottom (`low`) or the top. */
export interface BoardRegion {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
  cx: number;
  low: boolean;
}


function fitIn(g: BoardRegion, W: number, H: number): BoardRect {
  const w = Math.max(0, Math.min(g.x1 - g.x0, (g.y1 - g.y0) * ASPECT, W * 0.8, H * 0.3 * ASPECT)), h = w / ASPECT;
  const x = clamp(g.cx - w / 2, g.x0, g.x1 - w);
  return { x, y: g.low ? g.y1 - h : g.y0, w, h };
}

/**
 * What places a stage's board: its performers left to right at their settled spots (the lead, then the co-star),
 * the bubble each has in this scene, the ensemble members to centre over, the headline's foot
 * and a column beside a lone performer.
 */
export interface BoardPlace {
  spots: BoardSpot[];
  bubbles: (BubbleId | undefined)[];
  over: number[];
  top: number;
  beside?: BoardRegion;
}

const margin = (W: number, H: number): number => Math.round(Math.min(W, H) * 0.03);
const gapOf = (W: number, H: number): number => Math.round(Math.min(W, H) * 0.02);
/** A performer's head and body: from the top of its layout box or its crown, whichever is higher, to its feet. */
function bodyOf(s: BoardSpot): Box {
  const w = widthOf(s.rig, s.h), top = s.by - Math.max(s.h, crownOf(s.rig, s.h));
  return { x: s.x - w / 2, y: top, w, h: s.by - top };
}

/** Only current bodies and bubbles are layout obstacles. */
function obstacles(W: number, H: number, b: BoardPlace): Box[] {
  const out = b.spots.map(bodyOf);
  b.bubbles.forEach((kind, i) => {
    if (kind) out.push(bubbleLayout(kind, b.spots[i], b.spots[i].rig, W, H).body);
  });
  return out;
}

/** A headline over the co-star (staging.ts stageLabels): its largest size per frame shape, and its line height. */
export const LABEL_FS = { land: 110, tall: 120, square: 96 } as const, LABEL_LINE = 1.05;

/** The first baseline of `lines` lines of a headline over the co-star, `fs` pixels, resting on `y1`. */
export const labelTop = (y1: number, lines: number, fs: number): number => y1 - (lines - 1) * fs * LABEL_LINE - fs * 0.22;

/** Fit one independent diagram beside or above the current performers and bubbles. */
export function boardRect(W: number, H: number, b: BoardPlace): BoardRect {
  const { spots, over, beside } = b, m = margin(W, H), gap = gapOf(W, H), y0 = Math.max(b.top, m);
  const mid = over.length ? over.reduce((a, i) => a + spots[i].x, 0) / over.length : (spots[0].x + spots[spots.length - 1].x) / 2;
  const feet = Math.min(...spots.map((s) => s.by)) - gap;
  const blocked = obstacles(W, H, b).map((o) => ({ x: o.x - gap, y: o.y - gap, w: o.w + 2 * gap, h: o.h + 2 * gap }));
  const edges = [...new Set([m, W - m, ...(beside ? [beside.x0, beside.x1] : []), ...blocked.flatMap((o) => [o.x, o.x + o.w])]
    .map((x) => clamp(x, m, W - m)))].sort((a, c) => a - c);
  let best: BoardRect = { x: m, y: y0, w: 0, h: 0 };
  const consider = (x0: number, x1: number, top: number, bottom: number): void => {
    if (bottom <= top) return;
    const R = fitIn({ x0, x1, y0: top, y1: bottom, cx: mid, low: true }, W, H);
    if (R.w > best.w + 0.01 || (Math.abs(R.w - best.w) < 0.01 && Math.abs(R.x + R.w / 2 - mid) < Math.abs(best.x + best.w / 2 - mid))) best = R;
  };
  // Obstacle edges partition the free space; examine each vertical gap in each horizontal span.
  for (let i = 0; i < edges.length; i++) for (let j = i + 1; j < edges.length; j++) {
    const x0 = edges[i], x1 = edges[j];
    const bands = blocked.filter((o) => o.x < x1 && o.x + o.w > x0).sort((a, c) => a.y - c.y);
    let top = y0;
    for (const band of bands) {
      consider(x0, x1, top, Math.min(feet, band.y));
      top = Math.max(top, band.y + band.h);
    }
    consider(x0, x1, top, feet);
  }
  return best;
}

/** A layout's diagram column (scene.ts `Layout`) as a region beside a lone performer, down to its feet. */
export function besideRegion(L: { dx: number; dy: number; dw: number; left: boolean; characterY: number }, H: number): BoardRegion | undefined {
  if (!L.left) return undefined;
  return { x0: L.dx, x1: L.dx + L.dw, y0: L.dy, y1: L.characterY - Math.round(H * 0.02), cx: L.dx, low: false };
}

/** Draw a preset's nodes and links; label indices follow the documented node order. */
export function paintBoard(ctx: Ctx, st: BoardState, R: BoardRect, font: string, labels?: readonly string[]): void {
  const s = R.w / BOARD_W;
  if (s <= 0) return;
  ctx.save();
  ctx.translate(R.x, R.y);
  ctx.lineWidth = LINE * s;
  ctx.strokeStyle = '#7fb3b8';
  st.edges.forEach(([a, b], i) => {
    const from = st.boxes[a], to = st.boxes[b];
    ctx.globalAlpha = st.links[i];
    ctx.beginPath();
    ctx.moveTo(from.x * s, from.y * s);
    ctx.lineTo(to.x * s, to.y * s);
    ctx.stroke();
  });
  st.boxes.forEach((b, i) => {
    if (b.alpha <= 0) return;
    const label = labels?.[i] || '';
    setFont(ctx, 700, 100, font);
    const width = Math.max(1, ctx.measureText(label).width);
    const fs = Math.max(1, Math.floor(Math.min(BOX_H * 0.7 * s, 100 * NAME_FILL * BOX_W * s / width)));
    ctx.save();
    ctx.globalAlpha = b.alpha;
    ctx.translate(b.x * s, b.y * s);
    ctx.scale(b.scale, b.scale);
    rr(ctx, -BOX_W * s / 2, -BOX_H * s / 2, BOX_W * s, BOX_H * s, 0.04 * s);
    ctx.fillStyle = '#131d1f';
    ctx.fill();
    ctx.stroke();
    setFont(ctx, 700, fs, font);
    ctx.textAlign = 'center';
    ctx.fillStyle = '#f4fbfc';
    paintText(ctx, label, 0, fs * 0.36);
    ctx.restore();
  });
  ctx.restore();
}

export function drawBoard(ctx: Ctx, kind: BoardKind, lt: number, timing: ShotTiming, R: BoardRect, font: string, labels?: readonly string[]): void {
  paintBoard(ctx, boardState(kind, lt, timing, labels?.length || 3), R, font, labels);
}

export interface StageBoard extends BoardPlace { shift: number; labels?: readonly string[] }

export function drawStageBoard(ctx: Ctx, kind: BoardKind, lt: number, timing: ShotTiming, W: number, H: number, b: StageBoard, font: string): void {
  const R = boardRect(W, H, b);
  ctx.save();
  ctx.translate(b.shift, 0);
  drawBoard(ctx, kind, lt, timing, R, font, b.labels);
  ctx.restore();
}
