// Thumbnails: a video's still poster, drawn by the one engine for the Export sheet's preview and for
// the PNG the studio renders in Node. One clear focal character large in the foreground, cropped at the
// waist; the ensemble standing behind it; a bold title in the `screen` layout's slammed comic lettering;
// a bright backdrop under a vignette, and a white cutout outline round every performer so each pops.
// Recomposed for 16:9 (YouTube) and 9:16 (a Shorts or Reels cover). A still: every performer holds its
// settled pose at POSTER_T, and the rigs keep their whole-pixel scale. See motion-core/POSTER.md.

import { widthOf } from './acting.ts';
import { characterOf, drawCharacter, drawMissingCharacter } from './characters.ts';
import { paintText, fontOf, rr, setFont } from './draw.ts';
import { ENSEMBLE_MAX } from './ensemble.ts';
import type { ProjectCostar } from './project.ts';
import { ACCENT, BACKDROPS, backdrop } from './scene.ts';
import { badge, loud, stagingOf, toneOf } from './staging.ts';
import type { Badge, BackdropId, Character, Ctx, MotionEnv, PartSpec, RigCanvas } from './types.ts';

export type PosterFormat = '16:9' | '9:16';

/** A thumbnail's two sizes: a YouTube thumbnail and a Shorts or Reels cover. */
export const POSTER_FORMATS: Readonly<Record<PosterFormat, readonly [number, number]>> = {
  '16:9': [1280, 720],
  '9:16': [1080, 1920],
};

/** The moment every performer is drawn at: eyes open and the chest at rest, for every rig. */
export const POSTER_T = 0.6;
export const POSTER_BACKDROP: BackdropId = 'work';
/** A title line holds at most this many words, so it reads when the thumbnail is shrunk to 168 x 94. */
export const POSTER_WORDS = 3;

const INK = '#111214';
const HEX = /^#[0-9a-f]{6}$/i;

/** A member behind the lead, as stored: a performer plus an optional label over its head. */
export interface ProjectPosterMember extends ProjectCostar {
  badge?: { text?: string; tone?: string } | null;
}

/** A thumbnail as stored, after the host resolved each profile to its rig, palette and custom spec. */
export interface ProjectPoster {
  headline?: string;
  /** The pill over the title; empty or absent shows none. */
  eyebrow?: string;
  /** The eyebrow's colour, the workspace brand's accent (#rrggbb). */
  accent?: string;
  badges?: readonly { text?: string; tone?: string }[] | null;
  character?: string;
  palette?: string;
  pose?: string;
  expression?: string;
  custom?: PartSpec;
  ensemble?: readonly ProjectPosterMember[] | null;
  backdrop?: string;
}

/** One performer's look on the poster; it is drawn still, at POSTER_T. */
export interface PosterLook {
  character: string;
  palette: string;
  pose: string;
  expression: string;
  custom?: PartSpec;
  /** A member's label, drawn over its head in the title card's badge style. */
  badge?: Badge;
}

/** A thumbnail ready to draw. */
export interface Poster {
  headline: string;
  eyebrow: string;
  accent: string;
  badges: Badge[];
  lead: PosterLook;
  ensemble: PosterLook[];
  backdrop: BackdropId;
}

// The looks a performer takes when the thumbnail names none: the first of each list its rig has. The lead
// rests; members point or wave when those poses are available.
const LEAD_LOOK = { poses: ['rest', 'point', 'wave'], expressions: ['neutral', 'happy'] } as const;
const MEMBER_LOOK = { poses: ['point', 'wave', 'rest'], expressions: ['neutral', 'happy'] } as const;

const first = (wanted: readonly string[], has: readonly string[]): string => wanted.find((id) => has.includes(id)) ?? has[0];

function lookOf(s: ProjectCostar, fallback: { poses: readonly string[]; expressions: readonly string[] }): PosterLook {
  const rig = characterOf(s.character);
  return {
    character: s.character ?? rig.id,
    palette: s.palette !== undefined && rig.palettes.includes(s.palette) ? s.palette : rig.palettes[0],
    pose: s.pose !== undefined && rig.poses.includes(s.pose) ? s.pose : first(fallback.poses, rig.poses),
    expression: s.expression || first(fallback.expressions, Object.keys(rig.expressions)),
    ...(s.custom && { custom: s.custom }),
  };
}

/**
 * Fill a stored thumbnail out to a drawable Poster. Unknown or missing ids fall back to the poster's looks
 * (LEAD_LOOK, MEMBER_LOOK) for each rig, and the backdrop to `work`. An expression that is not built in
 * is kept, so a custom one (resolved by the host into `custom`) survives. At most ENSEMBLE_MAX members.
 */
export function resolvePoster(s: ProjectPoster): Poster {
  const members = Array.isArray(s.ensemble) ? s.ensemble.filter((m) => m && typeof m === 'object').slice(0, ENSEMBLE_MAX) : [];
  return {
    headline: typeof s.headline === 'string' ? s.headline.trim() : '',
    eyebrow: typeof s.eyebrow === 'string' ? s.eyebrow.trim() : '',
    accent: s.accent && HEX.test(s.accent) ? s.accent : ACCENT,
    badges: stagingOf({ badges: s.badges }).badges ?? [],
    lead: lookOf(s, LEAD_LOOK),
    ensemble: members.map((m) => {
      const label = m.badge ? stagingOf({ badges: [m.badge] }).badges?.[0] : undefined;
      return { ...lookOf(m, MEMBER_LOOK), ...(label && { badge: label }) };
    }),
    backdrop: BACKDROPS.includes(s.backdrop as BackdropId) ? (s.backdrop as BackdropId) : POSTER_BACKDROP,
  };
}

// ---------------------------------------------------------------- layout

/** A performer's spot: feet centred on (x, by), `h` tall at the whole-pixel scale `g`, `w` wide at rest. */
export interface PosterSlot {
  x: number;
  by: number;
  h: number;
  g: number;
  w: number;
}

/** The title's region: its left edge (or its centre line), top, width, height, largest size and lines. */
export interface PosterTitleBox {
  x: number;
  top: number;
  w: number;
  h: number;
  fs: number;
  lines: number;
  center: boolean;
}

export interface PosterLayout {
  title: PosterTitleBox;
  lead: PosterSlot;
  /** The ensemble, left to right. */
  members: PosterSlot[];
}

/** Rig pixels of the lead below the frame's bottom edge, over its rows: it is cropped at the waist. */
export const POSTER_CROP = 0.2;

const rowsOf = (rig: Character): number => rig.rows ?? 50;

function slotOf(rig: Character, x: number, by: number, g: number): PosterSlot {
  const h = g * rowsOf(rig);
  return { x: Math.round(x), by: Math.round(by), h, g, w: widthOf(rig, h) };
}

// Per format: the title's region; the lead's greatest height and width and its margin from the right edge;
// the ensemble's band: its left edge, where its feet stand and the highest its heads reach (below the
// title's region). A landscape band ends a little behind the lead; a tall one spans the frame.
const FRAMES = {
  land: {
    title: { x: 0.045, top: 0.065, w: 0.6, h: 0.42, fs: 0.2, lines: 3 },
    lead: { h: 1, w: 0.36, right: 0.03 },
    band: { x0: 0.035, by: 0.965, top: 0.54 },
  },
  tall: {
    title: { x: 0.06, top: 0.07, w: 0.88, h: 0.22, fs: 0.14, lines: 3 },
    lead: { h: 0.56, w: 0.7, right: 0.03 },
    band: { x0: 0.03, by: 0.5, top: 0.29 },
  },
} as const;

// A pose's arms and hands reach this much past the rig's width at rest (Character.aspect).
const REACH = 1.2;
// Neighbouring members may overlap by up to this share of their width before they all shrink a pixel size,
// and stand at most GAP of it apart.
const OVERLAP = 0.25, GAP = 0.2;

/**
 * Where a thumbnail's parts sit in a W x H frame. The lead stands bottom-right, as tall as the frame
 * allows at a whole-pixel scale and cropped at the waist (POSTER_CROP). The ensemble stands behind it,
 * feet level, every member at one pixel size (the tallest rig by its rows sets it), spread evenly across
 * its band and overlapping a little when the band is narrow. The title takes the top-left of a landscape
 * frame and the top of a tall one, above the ensemble's heads.
 */
export function posterLayout(W: number, H: number, lead: Character, members: readonly Character[]): PosterLayout {
  const land = W >= H, F = land ? FRAMES.land : FRAMES.tall;
  const gl = Math.max(1, Math.floor(Math.min((F.lead.h * H) / rowsOf(lead), (F.lead.w * W) / (REACH * widthOf(lead, rowsOf(lead))))));
  const hl = gl * rowsOf(lead), wl = widthOf(lead, hl);
  const leadSlot = slotOf(lead, W * (1 - F.lead.right) - (REACH * wl) / 2, H + POSTER_CROP * hl, gl);

  const x0 = W * F.band.x0, x1 = land ? leadSlot.x - wl * 0.35 : W - x0, by = H * F.band.by;
  const tallest = Math.max(1, ...members.map(rowsOf));
  const widths = (g: number): number[] => members.map((m) => widthOf(m, g * rowsOf(m)));
  const sum = (ws: number[]): number => ws.reduce((a, b) => a + b, 0);
  let gm = Math.max(1, Math.floor((by - H * F.band.top) / tallest));
  while (gm > 1 && sum(widths(gm)) * (1 - OVERLAP) + OVERLAP * Math.min(...widths(gm)) > x1 - x0) gm--;
  const ws = widths(gm), n = members.length, avg = sum(ws) / Math.max(1, n);
  const gap = n > 1 ? Math.min(GAP * avg, (x1 - x0 - sum(ws)) / (n - 1)) : 0;
  let left = (x0 + x1) / 2 - (sum(ws) + gap * (n - 1)) / 2;
  const memberSlots = members.map((m, i) => {
    const slot = slotOf(m, left + ws[i] / 2, by, gm);
    left += ws[i] + gap;
    return slot;
  });

  const T = F.title;
  return {
    title: { x: W * T.x, top: H * T.top, w: W * T.w, h: H * T.h, fs: Math.round(Math.min(W, H) * T.fs), lines: T.lines, center: !land },
    lead: leadSlot,
    members: memberSlots,
  };
}

// ---------------------------------------------------------------- the title

// The title leans like the `screen` layout's slammed headline at rest.
const TILT = -0.06;

// Words into lines of at most `cap` words, each as wide as fits in `maxW` at the current font.
function wrapWords(ctx: Ctx, words: readonly string[], maxW: number, cap: number): string[] {
  const out: string[] = [];
  let line: string[] = [];
  for (const w of words) {
    const next = [...line, w];
    if (line.length && (line.length >= cap || ctx.measureText(next.join(' ')).width > maxW)) {
      out.push(line.join(' '));
      line = [w];
    } else line = next;
  }
  if (line.length) out.push(line.join(' '));
  return out;
}

/** The title as drawn: its lines (upper case), size, line height, block and its tilted bounds in the frame. */
export interface PosterTitle {
  lines: string[];
  fs: number;
  lh: number;
  x: number;
  top: number;
  width: number;
  height: number;
  /** The block's extents once tilted, the eyebrow and badges included: [x0, y0, x1, y1]. */
  bounds: readonly [number, number, number, number];
}

const EYEBROW_K = 0.26, BADGE_K = 0.3;

/**
 * The largest title that fits its region: at most `lines` lines of at most POSTER_WORDS words (more per
 * line only when the title has more words than that allows), none wider than the region, the eyebrow and
 * badges included in its height. Empty when the headline is.
 */
export function posterTitle(ctx: Ctx, poster: Poster, T: PosterTitleBox, env: MotionEnv): PosterTitle {
  const font = fontOf(env), words = poster.headline.toUpperCase().split(/\s+/).filter(Boolean);
  const cap = Math.max(POSTER_WORDS, Math.ceil(words.length / T.lines));
  let fs = T.fs, lines: string[] = [];
  const extra = (size: number) => (poster.eyebrow ? size * EYEBROW_K * 2.2 : 0) + (poster.badges.length ? size * BADGE_K * 2.6 : 0);
  for (; ; fs = Math.floor(fs * 0.94)) {
    setFont(ctx, 900, fs, font);
    lines = wrapWords(ctx, words, T.w, cap);
    const fits = lines.length <= T.lines && lines.every((l) => ctx.measureText(l).width <= T.w)
      && lines.length * fs * 1.02 + extra(fs) <= T.h;
    if (fits || fs <= 14) break;
  }
  setFont(ctx, 900, fs, font);
  const lh = fs * 1.02, width = Math.max(0, ...lines.map((l) => ctx.measureText(l).width));
  const top = T.top + (poster.eyebrow ? fs * EYEBROW_K * 2.2 : 0), height = lines.length * lh;
  const x = T.center ? T.x + (T.w - width) / 2 : T.x;
  const y0 = T.top, y1 = top + height + (poster.badges.length ? fs * BADGE_K * 2.6 : 0) + fs * 0.12;
  const x0 = x - fs * 0.06, x1 = x + Math.max(width, badgesWidth(ctx, poster, fs, font)) + fs * 0.1;
  // The tilt turns the block about its centre; its corners bound what it covers.
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, c = Math.cos(TILT), s = Math.sin(TILT);
  const pts = [[x0, y0], [x1, y0], [x0, y1], [x1, y1]].map(([px, py]) => [cx + (px - cx) * c - (py - cy) * s, cy + (px - cx) * s + (py - cy) * c]);
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  return { lines, fs, lh, x, top, width, height, bounds: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] };
}

function badgesWidth(ctx: Ctx, poster: Poster, fs: number, font: string): number {
  if (!poster.badges.length) return 0;
  const fb = fs * BADGE_K;
  setFont(ctx, 900, fb, font);
  const w = poster.badges.reduce((sum, b) => sum + ctx.measureText(b.text).width + fb * 1.4, 0) + fb * 0.45 * (poster.badges.length - 1);
  setFont(ctx, 900, fs, font);
  return w;
}

function drawTitle(ctx: Ctx, poster: Poster, T: PosterTitleBox, env: MotionEnv): void {
  const P = posterTitle(ctx, poster, T, env), font = fontOf(env);
  if (!P.lines.length && !poster.eyebrow && !poster.badges.length) return;
  const [bx0, by0, bx1, by1] = P.bounds, cx = (bx0 + bx1) / 2, cy = (by0 + by1) / 2;
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(TILT);
  ctx.translate(-cx, -cy);
  ctx.textBaseline = 'alphabetic';
  ctx.textAlign = T.center ? 'center' : 'left';
  const x = T.center ? P.x + P.width / 2 : P.x;
  if (poster.eyebrow) {
    const fe = Math.round(P.fs * EYEBROW_K);
    setFont(ctx, 800, fe, font);
    const ew = ctx.measureText(poster.eyebrow).width, ex = T.center ? x - ew / 2 : x, ey = P.top - fe * 0.75;
    ctx.fillStyle = INK;
    rr(ctx, ex - fe * 0.5, ey - fe * 1.05, ew + fe, fe * 1.5, fe * 0.75);
    ctx.fill();
    ctx.fillStyle = poster.accent;
    paintText(ctx, poster.eyebrow, x, ey);
  }
  setFont(ctx, 900, P.fs, font);
  P.lines.forEach((l, i) => loud(ctx, l, x, P.top + P.fs * 0.8 + i * P.lh, P.fs));
  ctx.textAlign = 'left';
  if (poster.badges.length) {
    const fb = P.fs * BADGE_K, bh = fb * 1.6, gap = fb * 0.45;
    setFont(ctx, 900, fb, font);
    const ws = poster.badges.map((b) => ctx.measureText(b.text).width + fb * 1.4);
    const total = ws.reduce((a, b) => a + b, 0) + gap * (ws.length - 1);
    let left = T.center ? x - total / 2 : x;
    const y = P.top + P.height + fb * 0.5 + bh / 2;
    poster.badges.forEach((b, i) => {
      ctx.save();
      ctx.translate(left + ws[i] / 2, y);
      badge(ctx, b.text, ws[i], bh, fb, toneOf(b.tone, poster.accent), font, null);
      ctx.restore();
      left += ws[i] + gap;
    });
  }
  ctx.restore();
}

// ---------------------------------------------------------------- member labels

/** A member's label type size over that member's height, and its gap above the head in the same terms. */
const LABEL_K = 0.12, LABEL_GAP = 0.04;

/** Where a member's label sits: centred on (x, y), w x h, its type size `fb`. */
export interface PosterLabel {
  member: number;
  text: string;
  x: number;
  y: number;
  w: number;
  h: number;
  fb: number;
}

/**
 * Each labelled member's label: centred over its head (the top of its rig, using its declared crown), a
 * little above it, kept inside the frame's edges. Its size follows the member's, so the band's pixel size
 * sets it for every member alike.
 */
export function posterLabels(ctx: Ctx, poster: Poster, L: PosterLayout, W: number, env: MotionEnv): PosterLabel[] {
  const font = fontOf(env), out: PosterLabel[] = [];
  poster.ensemble.forEach((m, i) => {
    const s = L.members[i];
    if (!m.badge || !s) return;
    const fb = Math.max(12, Math.round(s.h * LABEL_K)), h = fb * 1.6;
    setFont(ctx, 900, fb, font);
    const w = ctx.measureText(m.badge.text).width + fb * 1.4, margin = fb * 0.5;
    const x = Math.min(W - margin - w / 2, Math.max(margin + w / 2, s.x));
    out.push({ member: i, text: m.badge.text, x, y: s.by - s.h - s.h * LABEL_GAP - h / 2, w, h, fb });
  });
  return out;
}

function drawLabels(ctx: Ctx, poster: Poster, L: PosterLayout, W: number, env: MotionEnv): void {
  const font = fontOf(env);
  for (const l of posterLabels(ctx, poster, L, W, env)) {
    setFont(ctx, 900, l.fb, font);
    ctx.save();
    ctx.translate(l.x, l.y);
    badge(ctx, l.text, l.w, l.h, l.fb, toneOf(poster.ensemble[l.member].badge!.tone, poster.accent), font, null);
    ctx.restore();
  }
}

// ---------------------------------------------------------------- cutouts

/** The white outline round each cutout, in frame pixels over the frame's short side (to whole rig pixels). */
export const POSTER_OUTLINE = 0.011;
// Solid pieces smaller than this many rig pixels (small highlights) get no outline of their own.
const SPECK = 6;

// A disc of offsets `r` rig pixels round, so the outline's corners round off in steps.
function disc(r: number): [number, number][] {
  const out: [number, number][] = [];
  for (let j = -r; j <= r; j++) for (let i = -r; i <= r; i++) if (i * i + j * j <= r * r + 1) out.push([i, j]);
  return out;
}

interface Layers {
  art: RigCanvas;
  mask: RigCanvas;
  ring: RigCanvas;
}

// Three frame-sized canvases per env and size; each paint clears them fully, so reuse stays pure.
const layers = new WeakMap<MotionEnv, Map<string, Layers>>();

function layersOf(env: MotionEnv, w: number, h: number): Layers {
  let sized = layers.get(env);
  if (!sized) layers.set(env, (sized = new Map()));
  const key = `${w}x${h}`;
  let l = sized.get(key);
  if (!l) sized.set(key, (l = { art: env.createCanvas(w, h), mask: env.createCanvas(w, h), ring: env.createCanvas(w, h) }));
  return l;
}

function cleared(canvas: RigCanvas, w: number, h: number): Ctx {
  const c = canvas.getContext('2d');
  if (!c) throw new Error('motion-core: env.createCanvas returned a canvas without a 2d context');
  c.setTransform(1, 0, 0, 1, 0, 0);
  c.globalCompositeOperation = 'source-over';
  c.clearRect(0, 0, w, h);
  return c;
}

// The solid silhouette (alpha of at least half) as opaque white into `out`, leaving out the soft ground
// shadows and highlights, and any piece smaller than SPECK rig pixels (4-connected).
function silhouette(src: Uint8ClampedArray, w: number, h: number, g: number, out: Uint8ClampedArray): void {
  const seen = new Uint8Array(w * h), piece = new Int32Array(w * h), min = SPECK * g * g;
  let tail = 0;
  const visit = (p: number): void => {
    if (seen[p] || src[p * 4 + 3] < 128) return;
    seen[p] = 1;
    piece[tail++] = p;
  };
  for (let p = 0; p < w * h; p++) {
    if (seen[p] || src[p * 4 + 3] < 128) continue;
    tail = 0;
    visit(p);
    for (let head = 0; head < tail; head++) {
      const q = piece[head], x = q % w;
      if (x > 0) visit(q - 1);
      if (x < w - 1) visit(q + 1);
      if (q >= w) visit(q - w);
      if (q < w * (h - 1)) visit(q + w);
    }
    if (tail >= min) for (let k = 0; k < tail; k++) out.fill(255, piece[k] * 4, piece[k] * 4 + 4);
  }
}

/**
 * Paints performers as one cutout: drawn alone, then laid over a white outline round their solid
 * silhouette, a whole number of their pixels `g` wide. The layers are frame-sized, so the host's
 * transform scales the result like everything else.
 */
function cutout(ctx: Ctx, env: MotionEnv, W: number, H: number, g: number, paint: (oc: Ctx) => void): void {
  const w = Math.ceil(W), h = Math.ceil(H), L = layersOf(env, w, h);
  const art = cleared(L.art, w, h);
  paint(art);
  const mask = cleared(L.mask, w, h), solid = mask.createImageData(w, h);
  silhouette(art.getImageData(0, 0, w, h).data, w, h, g, solid.data);
  mask.putImageData(solid, 0, 0);
  const ring = cleared(L.ring, w, h), r = Math.max(1, Math.round((POSTER_OUTLINE * Math.min(W, H)) / g));
  for (const [i, j] of disc(r)) ring.drawImage(L.mask, i * g, j * g);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(L.ring, 0, 0, w, h);
  ctx.drawImage(L.art, 0, 0, w, h);
  ctx.restore();
}

// ---------------------------------------------------------------- the poster

const still = (look: PosterLook) => ({ ...look, move: 'idle', t: POSTER_T });

/** The backdrop under a thumbnail: the scene backdrop, still, under a vignette. */
export function drawPosterBackdrop(ctx: Ctx, id: BackdropId, W: number, H: number): void {
  backdrop(ctx, id, W, H, POSTER_T);
  const v = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.35, W / 2, H / 2, Math.hypot(W, H) * 0.6);
  v.addColorStop(0, 'rgba(0,0,0,0)');
  v.addColorStop(1, 'rgba(0,0,0,.38)');
  ctx.fillStyle = v;
  ctx.fillRect(0, 0, W, H);
}

/**
 * A thumbnail in W x H space: the backdrop and its vignette, the ensemble (outer members first, so the
 * middle stands in front), the lead in front of them, the members' labels, and the title on top. Deterministic: the same
 * poster draws the same pixels in the browser and in Node.
 */
export function drawPoster(ctx: Ctx, poster: Poster, W: number, H: number, env: MotionEnv): void {
  const lead = characterOf(poster.lead.character), members = poster.ensemble.map((m) => characterOf(m.character));
  const L = posterLayout(W, H, lead, members);
  ctx.save();
  ctx.textBaseline = 'alphabetic';
  drawPosterBackdrop(ctx, poster.backdrop, W, H);
  if (members.length) {
    // Outer members first, so the middle one stands in front.
    const mid = (members.length - 1) / 2;
    const order = [...members.keys()].sort((a, b) => Math.abs(b - mid) - Math.abs(a - mid) || a - b);
    cutout(ctx, env, W, H, L.members[0].g, (oc) => {
      for (const i of order) drawCharacter(oc, L.members[i].x, L.members[i].by, L.members[i].h, still(poster.ensemble[i]), env);
    });
  }
  // The lead stands right of the centre, so it is mirrored to face into the frame (a rig faces screen-right),
  // about a whole-pixel origin so it stays crisp.
  cutout(ctx, env, W, H, L.lead.g, (oc) => {
    oc.save();
    oc.translate(2 * L.lead.x, 0);
    oc.scale(-1, 1);
    drawCharacter(oc, L.lead.x, L.lead.by, L.lead.h, still(poster.lead), env, false);
    oc.restore();
    drawMissingCharacter(oc, L.lead.x, L.lead.by, L.lead.h, poster.lead.character, env);
  });
  drawLabels(ctx, poster, L, W, env);
  drawTitle(ctx, poster, L.title, env);
  ctx.restore();
}
