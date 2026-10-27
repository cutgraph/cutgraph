import type { Character, CharacterState, Ctx, MotionEnv, PartSpec, RigCanvas } from './types.ts';
import { pixelImage } from './draw.ts';

type Joint = readonly [number, number, number];
export type Ramp = readonly [string, string, string, string];
export const OUTLINE = '#111214';
export const PAPER: Ramp = ['#ffffff', '#eef2f4', '#c9d2d8', '#93a2ad'];
const W = 45, H = 50, MID = 22;
const N4 = [[1, 0], [-1, 0], [0, 1], [0, -1]] as const;

export const LENS_EXPRESSIONS: Readonly<Record<string, PartSpec>> = {
  neutral: { eyes: 'open', mouth: 'smile' },
  happy: { eyes: 'arc', mouth: 'grin' },
  excited: { eyes: 'wide', mouth: 'grin' },
  surprised: { eyes: 'tall', mouth: 'round' },
  curious: { eyes: 'side', mouth: 'smile' },
  focused: { eyes: 'narrow', mouth: 'flat' },
  sleepy: { eyes: 'closed', mouth: 'small' },
  idea: { eyes: 'wide', mouth: 'smile' },
  confused: { eyes: 'uneven', mouth: 'crooked' },
  determined: { eyes: 'narrow', mouth: 'smile' },
  proud: { eyes: 'arc', mouth: 'smile' },
};
export const LENS_EYES: readonly string[] = ['open', 'arc', 'wide', 'tall', 'side', 'narrow', 'closed', 'uneven'];
export const LENS_MOUTHS: readonly string[] = ['smile', 'grin', 'round', 'flat', 'small', 'crooked'];
const PARTS = { eyes: LENS_EYES, mouth: LENS_MOUTHS };
export const LENS_POSES: readonly string[] = ['rest', 'point', 'hold', 'cheer', 'shrug', 'think', 'wave', 'hips', 'flex'];
export const LENS_MOVES: readonly string[] = ['idle', 'enter', 'hop', 'walk', 'nod', 'think', 'wave', 'celebrate', 'sway'];

function tone(nx: number, ny: number, ramp: Ramp): string {
  const light = -0.5 * ny + 0.86 * Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
  return ramp[light > 0.9 ? 0 : light > 0.45 ? 1 : light > 0.02 ? 2 : 3];
}

export function pixel(ctx: Ctx, x: number, y: number, color: string): void {
  ctx.fillStyle = color;
  ctx.fillRect(Math.round(x), Math.round(y), 1, 1);
}

type Surface = (x: number, y: number) => readonly [number, number, number];

function fill(ctx: Ctx, box: readonly number[], at: Surface, ramp: Ramp): void {
  for (let y = Math.max(0, Math.floor(box[1]) - 1); y <= Math.min(H - 1, Math.ceil(box[3]) + 1); y++) {
    for (let x = Math.max(0, Math.floor(box[0]) - 1); x <= Math.min(W - 1, Math.ceil(box[2]) + 1); x++) {
      const [d, nx, ny] = at(x, y);
      if (d <= 0) pixel(ctx, x, y, tone(nx, ny, ramp));
      else if (N4.some(([dx, dy]) => at(x + dx, y + dy)[0] <= 0)) pixel(ctx, x, y, OUTLINE);
    }
  }
}

export function oval(ctx: Ctx, x: number, y: number, rx: number, ry: number, ramp: Ramp, power = 2): void {
  fill(ctx, [x - rx, y - ry, x + rx, y + ry], (px, py) => {
    const u = (px - x) / rx, v = (py - y) / ry;
    return [Math.abs(u) ** power + Math.abs(v) ** power - 1, u, v];
  }, ramp);
}

function capsule(ctx: Ctx, joints: readonly Joint[], ramp: Ramp): void {
  const pad = Math.max(...joints.map((j) => j[2]));
  const xs = joints.map((j) => j[0]), ys = joints.map((j) => j[1]);
  fill(ctx, [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad], (x, y) => {
    let best = Infinity, nx = 0, ny = 0;
    for (let k = 0; k < joints.length - 1; k++) {
      const [ax, ay, ar] = joints[k], [bx, by, br] = joints[k + 1];
      const vx = bx - ax, vy = by - ay;
      const u = Math.max(0, Math.min(1, ((x - ax) * vx + (y - ay) * vy) / (vx * vx + vy * vy || 1)));
      const r = ar + u * (br - ar), dx = x - ax - u * vx, dy = y - ay - u * vy;
      const d = Math.hypot(dx, dy) - r;
      if (d < best) { best = d; nx = dx / r; ny = dy / r; }
    }
    return [best, nx, ny];
  }, ramp);
}

const ARMS: Readonly<Record<string, readonly [readonly Joint[], readonly Joint[]]>> = {
  hips: [[[11, 34, 1.5], [7, 37, 1.4], [13, 39, 2]], [[33, 34, 1.5], [37, 37, 1.4], [31, 39, 2]]],
  flex: [[[11, 34, 1.5], [6, 31, 1.4], [9, 26, 2]], [[33, 34, 1.5], [38, 31, 1.4], [35, 26, 2]]],
  rest: [[[11, 34, 1.5], [9, 38, 1.4], [10, 41, 2]], [[33, 34, 1.5], [35, 38, 1.4], [34, 41, 2]]],
  point: [[[11, 34, 1.5], [9, 38, 1.4], [10, 41, 2]], [[33, 34, 1.5], [35, 33, 1.4], [39, 33, 2]]],
  hold: [[[11, 34, 1.5], [11, 40, 1.4], [19, 39, 2]], [[33, 34, 1.5], [33, 40, 1.4], [25, 39, 2]]],
  cheer: [[[11, 34, 1.5], [7, 30, 1.4], [7, 25, 2]], [[33, 34, 1.5], [37, 30, 1.4], [37, 25, 2]]],
  shrug: [[[11, 34, 1.5], [7, 36, 1.4], [6, 32, 2]], [[33, 34, 1.5], [37, 36, 1.4], [38, 32, 2]]],
  think: [[[11, 34, 1.5], [9, 38, 1.4], [10, 41, 2]], [[33, 34, 1.5], [32, 39, 1.4], [27, 32, 2]]],
  wave: [[[11, 34, 1.5], [9, 38, 1.4], [10, 41, 2]], [[33, 34, 1.5], [37, 29, 1.4], [37, 23, 2]]],
};

function face(ctx: Ctx, spec: PartSpec, y: number, t: number): void {
  const kind = t % 3.7 < 0.1 ? 'closed' : spec.eyes;
  for (const [k, x] of [17, 27].entries()) {
    const h = kind === 'tall' ? 3 : kind === 'wide' ? 2.8 : kind === 'uneven' && k === 1 ? 1 : 2;
    if (kind === 'arc' || kind === 'closed' || kind === 'narrow') {
      for (let dx = -2; dx <= 2; dx++) pixel(ctx, x + dx, y + (kind === 'arc' ? Math.abs(dx) > 1 ? 1 : 0 : kind === 'closed' ? Math.abs(dx) < 2 ? 1 : 0 : 0), OUTLINE);
    } else {
      oval(ctx, x, y, kind === 'wide' ? 2.7 : 2, h, PAPER);
      pixel(ctx, x + (kind === 'side' ? 1 : 0), y + 1, OUTLINE);
      pixel(ctx, x - 1, y - 1, PAPER[0]);
    }
  }
  const my = y + 6;
  if (spec.mouth === 'round') oval(ctx, MID, my, 1.5, 2, [OUTLINE, OUTLINE, OUTLINE, OUTLINE]);
  else if (spec.mouth === 'grin') { oval(ctx, MID, my, 3.5, 1.5, PAPER); }
  else {
    const w = spec.mouth === 'small' ? 1 : 3;
    for (let dx = -w; dx <= w; dx++) pixel(ctx, MID + dx, my + (spec.mouth === 'flat' ? 0 : spec.mouth === 'crooked' ? Math.round(dx / 2) : Math.abs(dx) < w ? 1 : 0), OUTLINE);
  }
}

export interface Motion {
  dx: number; dy: number; sy: number; chest: number; head: number; lag: number; step: number; swing: number; pose?: string;
}

function motion(move: string, t: number): Motion {
  const m: Motion = { dx: 0, dy: 0, sy: 1, chest: Math.sin(t * Math.PI / 1.2) > 0.4 ? -1 : 0, head: 0, lag: 0, step: -1, swing: 0 };
  if (move === 'hop' || move === 'celebrate') {
    const p = (t % 1.2) / 1.2;
    m.chest = 0;
    if (p < 0.15) m.sy = 1 - 0.1 * p / 0.15;
    else if (p < 0.75) {
      const u = (p - 0.15) / 0.6;
      m.dy = -9 * Math.sin(u * Math.PI);
      m.lag = u < 0.5 ? 1 : -2;
    } else m.sy = 1 - 0.16 * Math.max(0, 1 - (p - 0.75) / 0.2);
    if (move === 'celebrate') { m.pose = 'cheer'; m.swing = Math.round(Math.sin(t * 12)); }
  } else if (move === 'walk' || move === 'enter') {
    m.step = Math.floor(t * 3.3) % 2;
    m.chest = Math.floor(t * 8) % 2 ? -1 : 0;
    if (move === 'enter') { m.dx = -58 * (1 - Math.min(1, t / 1.1)) ** 3; if (t >= 1.1) m.step = -1; }
  } else if (move === 'nod') m.head = Math.round(Math.sin(t * 6) * 2);
  else if (move === 'think') { m.pose = 'think'; m.head = Math.round(Math.sin(t * 3)); }
  else if (move === 'sway') { m.pose = 'flex'; m.swing = Math.round(Math.sin(t * 8) * 2); }
  else if (move === 'wave') { m.pose = 'wave'; m.swing = Math.round(Math.sin(t * 9) * 2); }
  return m;
}

export function rigDraw(ramp: Ramp, body: (ctx: Ctx, m: Motion) => number): Character['draw'] {
  const layers = new WeakMap<MotionEnv, RigCanvas>();
  return (ctx, cx, by, h, state: CharacterState, env) => {
    const t = state.t ?? 0, g = Math.max(1, Math.floor(h / H));
    const m = motion(LENS_MOVES.includes(state.move as typeof LENS_MOVES[number]) ? state.move! : 'idle', t);
    const pose = m.pose ?? (LENS_POSES.includes(state.pose as typeof LENS_POSES[number]) ? state.pose! : 'rest');
    const custom = state.custom;
    const valid = custom && Object.keys(custom).length === 2 && Object.entries(PARTS).every(([part, options]) => options.includes(custom[part]));
    const spec = Object.prototype.hasOwnProperty.call(LENS_EXPRESSIONS, state.expression ?? '') ? LENS_EXPRESSIONS[state.expression!] : valid ? custom : LENS_EXPRESSIONS.neutral;
    let canvas = layers.get(env);
    if (!canvas) { canvas = env.createCanvas(W, H); layers.set(env, canvas); }
    const oc = canvas.getContext('2d');
    if (!oc) throw new Error('Rig canvas needs a 2d context');
    oc.clearRect(0, 0, W, H);
    for (const [k, x] of [17, 27].entries()) {
      const lift = m.step === k ? -2 : 0;
      capsule(oc, [[x, 40 + m.chest, 1.5], [x, 46 + lift, 1.4]], ramp);
      oval(oc, x, 47 + lift, 3, 1, ramp);
    }
    const fy = body(oc, m);
    face(oc, spec, fy, t);
    ARMS[pose].forEach((joints, k) => capsule(oc, joints.map(([x, y, r]): Joint => [x + (k ? m.swing : -m.swing), y + m.chest, r]), ramp));
    const lift = state.lift ?? 0, x = Math.round(cx + m.dx * g);
    ctx.save();
    ctx.fillStyle = 'rgba(0,0,0,.28)';
    ctx.beginPath();
    ctx.ellipse(x, by, Math.max(4, 13 + (m.dy - lift / g) * 0.6) * g, 2 * g, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.translate(x, Math.round(by + m.dy * g - lift));
    ctx.scale(1 / m.sy, m.sy);
    ctx.imageSmoothingEnabled = false;
    pixelImage(ctx, canvas, -Math.floor((MID + 0.5) * g), -H * g, W * g, H * g, env);
    ctx.restore();
  };
}
