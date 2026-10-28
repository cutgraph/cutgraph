import { drawBubble } from './acting.ts';
import { stagedBackdrop } from './backdrops.ts';
import { BRIEF_TALL, BRIEF_WIDE, drawBrief } from './brief.ts';
import { characterOf, drawCharacter, performer } from './characters.ts';
import { clamp, ease, fontOf, prog, rr, setFont, shadow, text, wrap } from './draw.ts';
import { besideRegion, drawStageBoard, isBoard } from './ideas.ts';
import { GRID_DEPTH, MOTE_DEPTH, panAt, panDistance, panExtras, panShift } from './pan.ts';
import type {
  BackdropId, Ctx, DiagramId, FontEnv, Format, Layout, MotionEnv, Shot, ShotTiming, TextRun,
} from './types.ts';

export const FORMATS: Readonly<Record<Format, readonly [number, number]>> = {
  '16:9': [1920, 1080],
  '9:16': [1080, 1920],
  '1:1': [1080, 1080],
};

/**
 * Device pixels per design pixel of a W x H frame. motion-core is designed in the 1080-class pixels of
 * FORMATS, and every frame draws that same design at its own size: a 4K export gains detail, and a
 * 720p video's preview shows exactly what its 1080 export frames, never a layout of its own.
 */
export const designScale = (W: number, H: number): number => Math.min(W, H) / 1080;

export const BACKDROPS: readonly BackdropId[] = ['chaos', 'work', 'route', 'proof', 'result', 'black', 'vision'];
export const DIAGRAMS: readonly DiagramId[] = [
  'scatter', 'blank', 'brief', 'route', 'checks', 'glow', 'none', 'grid', 'chain', 'radial', 'tree', 'list',
];

const BACKDROP: Readonly<Record<BackdropId, string>> = {
  chaos: '#1f1c22', work: '#1a211d', route: '#162029', proof: '#14221f', result: '#17222c', black: '#000000', vision: '#ffd23f',
};

/** The backdrop at time `t`; `shift` is a pan's world offset (pan.ts), its dot grid and motes following in parallax. */
export function backdrop(ctx: Ctx, id: BackdropId, W: number, H: number, t: number, shift = 0): void {
  if (stagedBackdrop(ctx, id, W, H, t)) return;
  ctx.fillStyle = BACKDROP[id] ?? BACKDROP.route;
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = 'rgba(255,255,255,.05)';
  const gx = (((30 + Math.round(shift * GRID_DEPTH)) % 48) + 48) % 48, mx = Math.round(shift * MOTE_DEPTH);
  for (let y = 30; y < H; y += 48) for (let x = gx; x < W; x += 48) ctx.fillRect(x, y, 3, 3);
  ctx.fillStyle = 'rgba(245,197,24,.10)';
  for (let k = 0; k < 9; k++) {
    const x = (((k * 263 + 90 + mx) % W) + W) % W, y = H - ((t * (18 + k * 3) + k * 211) % (H + 40));
    ctx.fillRect(Math.round(x), Math.round(y), 8, 8);
  }
  const v = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.3, W / 2, H / 2, Math.max(W, H) * 0.75);
  v.addColorStop(0, 'rgba(0,0,0,0)');
  v.addColorStop(1, 'rgba(0,0,0,.45)');
  ctx.fillStyle = v;
  ctx.fillRect(0, 0, W, H);
}

export function layoutFor(W: number, H: number): Layout {
  if (W > H) {
    return { W, H, characterX: W * 0.23, characterY: H * 0.8, characterH: H * 0.6, tx: W * 0.44, ty: H * 0.22, tw: W * 0.5, fs: 80,
      dx: W * 0.44, dy: H * 0.52, dw: W * 0.48, left: true, capY: H * 0.935, capFs: 46 };
  }
  if (W < H) {
    return { W, H, characterX: W * 0.5, characterY: H * 0.79, characterH: H * 0.33, tx: W * 0.09, ty: H * 0.12, tw: W * 0.82, fs: 96,
      dx: W * 0.08, dy: H * 0.34, dw: W * 0.84, left: false, capY: H * 0.9, capFs: 58 };
  }
  return { W, H, characterX: W * 0.27, characterY: H * 0.8, characterH: H * 0.44, tx: W * 0.08, ty: H * 0.13, tw: W * 0.84, fs: 72,
    dx: W * 0.5, dy: H * 0.46, dw: W * 0.44, left: true, capY: H * 0.935, capFs: 44 };
}

/** Where each element of a shot sits for a format; the vertical format re-composes rather than crops. */
export const layout = (format: Format): Layout => layoutFor(...FORMATS[format]);

/** Config defaults, mirrored for browser and Node rendering; checked by the Python parity test. */
export const EYEBROW = '';
export const ACCENT = '#f5c518';

/**
 * The headline's text runs at rest (fully faded in): the eyebrow (none when empty), then each wrapped
 * line. The lines keep their place without an eyebrow.
 */
export function headlineRuns(ctx: Ctx, s: string, L: Layout, env?: FontEnv, eyebrow = EYEBROW, accent = ACCENT): TextRun[] {
  const runs: TextRun[] = eyebrow
    ? [{ text: eyebrow, x: L.tx, y: L.ty, size: Math.round(L.fs * 0.32), weight: 800, color: accent }]
    : [];
  setFont(ctx, 700, L.fs, fontOf(env));
  wrap(ctx, s, L.tw).forEach((l, i) => {
    runs.push({ text: l, x: L.tx, y: L.ty + L.fs * 1.12 + i * L.fs * 1.08, size: L.fs, weight: 700, color: '#eef2f4' });
  });
  if (L.center) {
    for (const r of runs) {
      setFont(ctx, r.weight, r.size, fontOf(env));
      r.x = L.tx + (L.tw - ctx.measureText(r.text).width) / 2;
    }
  }
  return runs;
}

/** How far down the headline's text reaches where it draws (its eyebrow and every line); 0 when it is hidden. */
export function headlineFoot(ctx: Ctx, shot: Pick<Shot, 'headline' | 'eyebrow' | 'accent' | 'hideHeadline'>, L: Layout, env: FontEnv): number {
  if (shot.hideHeadline) return 0;
  return Math.max(0, ...headlineRuns(ctx, shot.headline, L, env, shot.eyebrow, shot.accent).map((r) => r.y + r.size * 0.3));
}

export function headline(
  ctx: Ctx, shot: Pick<Shot, 'headline' | 'eyebrow' | 'accent' | 'hideHeadline'>, lt: number, at: number, L: Layout, env: FontEnv,
): void {
  if (shot.hideHeadline) return;
  const p = ease.out(prog(lt, at, 0.5));
  if (p <= 0) return;
  ctx.save();
  ctx.globalAlpha = p;
  const dy = (1 - p) * 34, font = fontOf(env);
  for (const r of headlineRuns(ctx, shot.headline, L, env, shot.eyebrow, shot.accent)) {
    text(ctx, r.text, r.x, r.y + dy, r.size, r.color, font, r.weight);
  }
  ctx.restore();
}

interface BoxStyle {
  alpha?: number;
  rot?: number;
  scale?: number;
  glow?: number;
  fill?: string | null;
  stroke?: string;
  dash?: number[] | null;
}

function box(ctx: Ctx, x: number, y: number, w: number, h: number, o: BoxStyle = {}): void {
  ctx.save();
  ctx.globalAlpha = o.alpha ?? 1;
  ctx.translate(x + w / 2, y + h / 2);
  ctx.rotate(o.rot || 0);
  ctx.scale(o.scale ?? 1, o.scale ?? 1);
  if (o.glow) shadow(ctx, '#79d5df', 50 * o.glow);
  rr(ctx, -w / 2, -h / 2, w, h, 16);
  ctx.fillStyle = o.fill || 'rgba(121,213,223,.08)';
  ctx.fill();
  ctx.setLineDash(o.dash || []);
  ctx.lineWidth = 5;
  ctx.strokeStyle = o.stroke || '#7fb3b8';
  ctx.stroke();
  ctx.restore();
}

function tick(ctx: Ctx, cx: number, cy: number, k: number, s: number): void {
  ctx.save();
  ctx.strokeStyle = '#90caa5';
  ctx.lineWidth = 12 * s;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.setLineDash([140 * s, 140 * s]);
  ctx.lineDashOffset = 140 * s * (1 - k);
  ctx.beginPath();
  ctx.moveTo(cx - 34 * s, cy);
  ctx.lineTo(cx - 8 * s, cy + 26 * s);
  ctx.lineTo(cx + 38 * s, cy - 28 * s);
  ctx.stroke();
  ctx.restore();
}

/** Where a layout's diagram row sits: three `bw` x `bh` boxes, `gap` apart, `total` wide from (X, Y). */
export function diagramRow(L: Layout): { X: number; Y: number; bw: number; bh: number; gap: number; total: number } {
  const bw = Math.min(200, L.dw / 3.9), gap = bw * 0.45, bh = bw * 0.66, total = bw * 3 + gap * 2;
  return { X: L.left ? L.dx : L.dx + (L.dw - total) / 2, Y: L.dy, bw, bh, gap, total };
}

/** The classic diagrams over a layout's diagram row; `none` and the independent diagram (ideas.ts) draw nothing here. */
export function diagram(ctx: Ctx, kind: DiagramId, lt: number, cue: number, L: Layout, font: string): void {
  if (kind === 'none' || isBoard(kind)) return;
  const { X, Y, bw, bh, gap, total } = diagramRow(L), s = bw / 200;
  const xs = [0, 1, 2].map((i) => X + i * (bw + gap));
  const c = ease.out(prog(lt, cue, 0.8));
  if (kind === 'scatter') {
    ([[-30, 40, -0.18], [40, -50, 0.22], [-10, 70, -0.3]] as const).forEach(([dx, dy, r], i) => {
      const k = 1 + c * 1.8, j = Math.sin(lt * 9 + i * 2) * 5;
      box(ctx, xs[i] + dx * k * s + j, Y + dy * k * s, bw, bh, { rot: r * k, alpha: 1 - c * 0.35, stroke: '#c0876d' });
    });
    return;
  }
  if (kind === 'brief') {
    const p = ease.back(prog(lt, cue, 0.55));
    if (p <= 0) return;
    const w = bw * BRIEF_WIDE, h = bw * BRIEF_TALL;
    drawBrief(ctx, X + (L.left ? 0 : (total - w) / 2), Y, w, h, s, p, font);
    return;
  }
  const pop = (i: number): number => (kind === 'blank' ? ease.out(prog(lt, cue + i * 0.15, 0.4)) : 1);
  ctx.save();
  ctx.strokeStyle = '#7fb3b8';
  ctx.lineWidth = 5;
  if (kind !== 'blank') {
    xs.slice(0, 2).forEach((x, i) => {
      const seg = kind === 'route' ? clamp(c * 2 - i) : 1;
      if (seg <= 0) return;
      ctx.beginPath();
      ctx.moveTo(x + bw, Y + bh / 2);
      ctx.lineTo(x + bw + gap * seg, Y + bh / 2);
      ctx.stroke();
    });
  }
  ctx.restore();
  xs.forEach((x, i) => {
    const p = pop(i);
    if (p <= 0) return;
    const glow = kind === 'glow' ? clamp(c * 3 - i) : 0;
    box(ctx, x, Y, bw, bh, {
      alpha: p, glow, dash: kind === 'blank' ? [14, 12] : null,
      fill: glow ? 'rgba(121,213,223,' + (0.1 + glow * 0.32) + ')' : null,
    });
    if (kind === 'blank') {
      text(ctx, '?', x + bw / 2, Y + bh * 0.66, Math.round(60 * s), 'rgba(238,242,244,' + 0.6 * p + ')', font, 700, 'center');
    }
    if (kind === 'checks') {
      const k = clamp(c * 3 - i);
      if (k > 0) tick(ctx, x + bw / 2, Y + bh / 2, k, s);
    }
  });
  if (kind === 'route' && c >= 1) {
    const loop = ((lt - cue - 0.8) * 0.6) % 1;
    ctx.save();
    ctx.fillStyle = ACCENT;
    shadow(ctx, ACCENT, 24);
    ctx.beginPath();
    ctx.arc(X + bw / 2 + loop * 2 * (bw + gap), Y + bh / 2, 14 * s, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
  if (kind === 'glow' && c > 0.3) {
    ctx.fillStyle = ACCENT;
    for (let k = 0; k < 18; k++) {
      const fx = X + ((k * 97) % total), fy = Y - 160 * s + ((lt * (90 + k * 7) + k * 53) % (bh + 260 * s));
      ctx.globalAlpha = 0.7;
      ctx.fillRect(Math.round(fx), Math.round(fy), 10 * s, 10 * s);
    }
    ctx.globalAlpha = 1;
  }
}

/**
 * One animated shot at scene-local time `lt`: backdrop, diagram (or the independent diagram), its character, its
 * bubble and the headline, in W x H space. A pan carries the stage in from the left (pan.ts).
 */
export function drawShot(
  ctx: Ctx, shot: Shot, lt: number, timing: ShotTiming, W: number, H: number, env: MotionEnv,
): void {
  const L = layoutFor(W, H), rig = characterOf(shot.character), cue = timing.cueT ?? 1;
  const pan = panAt(shot.camera, lt, timing.endT), shift = panShift(pan, pan ? panDistance(L.characterX, L.characterH, rig, W) : 0);
  const spot = { x: L.characterX + shift, by: L.characterY, h: L.characterH };
  ctx.save();
  ctx.textBaseline = 'alphabetic';
  backdrop(ctx, shot.backdrop, W, H, lt, shift);
  if (pan) panExtras(ctx, W, H, L.characterY, shift, pan.alpha);
  if (isBoard(shot.diagram)) {
    drawStageBoard(ctx, shot.diagram, lt, timing, W, H, {
      spots: [{ x: L.characterX, by: L.characterY, h: L.characterH, rig }], bubbles: [shot.bubble], over: [],
      top: headlineFoot(ctx, shot, L, env), shift, beside: besideRegion(L, H), labels: shot.boardLabels,
    }, fontOf(env));
  } else diagram(ctx, shot.diagram, lt, cue, L, fontOf(env));
  drawCharacter(ctx, spot.x, spot.by, spot.h, performer(shot, lt), env);
  if (shot.bubble) drawBubble(ctx, shot.bubble, spot, rig, lt - cue, W, H, env);
  headline(ctx, shot, lt, timing.firstT ?? 0.4, L, env);
  ctx.restore();
}
