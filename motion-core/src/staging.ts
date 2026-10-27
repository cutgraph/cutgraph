// Independent layouts and bounded cameras shared by preview and export.
// All timing is scene-local. See motion-core/STAGING.md.

import { bubbleLayout, widthOf } from './acting.ts';
import { characterOf, drawCharacter, drawMissingCharacter, performer } from './characters.ts';
import { cameraPixels, clamp, ease, paintText, fontOf, prog, rr, setFont, shadow, wrap } from './draw.ts';
import { duoLayout, overCostar, type DuoSlot } from './duo.ts';
import { ENSEMBLE_MAX, ensembleLayout, memberSlots } from './ensemble.ts';
import {
  BOARD_LABEL_MAX, BOARD_LABELS_MAX, LABEL_FS, LABEL_LINE, boardRect, isBoard, labelTop, type BoardRect, type BoardSpot,
} from './ideas.ts';
import { ACCENT, backdrop } from './scene.ts';
import type {
  Badge, BadgeTone, BubbleId, Camera, Character, Ctx, HeadlineSpot, MotionEnv, PhoneImage, RigCanvas, Shot, ShotTiming, StageLayout,
} from './types.ts';

export const LAYOUTS: readonly StageLayout[] = ['stage', 'screen', 'title', 'signoff'];
export const CAMERAS: readonly Camera[] = ['push-in', 'pull-out', 'pan'];
/** Where a stage shot's headline can go instead of its own place. */
export const HEADLINE_SPOTS: readonly HeadlineSpot[] = ['costar'];
export const BADGE_TONES: readonly BadgeTone[] = ['ink', 'paper', 'accent'];
/** The sign-off's pixel letters when a shot names none. */
export const MARK = '';
export const BADGES_MAX = 4;
export const MARK_MAX = 4;
/** Phones a `screen` shot draws beside phone 1 (the hero), at most: `screen.more`. */
export const SCREEN_MORE_MAX = 2;

const INK = '#111214';
const WHITE = '#f4f6f7';

// ---------------------------------------------------------------- the stored fields

/** A stored shot's staging fields, unchecked (ProjectShot carries them). */
export interface StoredStaging {
  camera?: string;
  layout?: string;
  /** `more` is stored as asset ids; a host that resolved one passes `{asset, image}` in its place. */
  screen?: { asset?: string; image?: string; more?: readonly (string | { asset?: string; image?: string })[] | null } | null;
  badges?: readonly { text?: string; tone?: string }[] | null;
  mark?: string;
  headlineAt?: string;
  boardLabels?: readonly unknown[] | null;
}

export type Staging = Pick<Shot, 'camera' | 'layout' | 'screen' | 'badges' | 'mark' | 'headlineAt' | 'boardLabels'>;

const str = (v: unknown): string => (typeof v === 'string' ? v.trim() : '');
const has = (o: object, key: string): boolean => Object.prototype.hasOwnProperty.call(o, key);

function phoneImage(p: unknown): PhoneImage | null {
  const o = typeof p === 'string' ? { asset: p } : p && typeof p === 'object' ? (p as PhoneImage) : {};
  const asset = str(o.asset), image = str(o.image);
  return asset || image ? { ...(asset && { asset }), ...(image && { image }) } : null;
}

/**
 * The drawable staging of a stored shot: unknown ids, blank badges and empty fields are dropped, `stage`
 * (the default layout) is left out, a badge's unknown tone is ink and the mark keeps the characters
 * its pixel face has (MARK_CHARS), in upper case. A screen's `more` keeps up to SCREEN_MORE_MAX filled
 * phones, and only beside phone 1. The board's labels keep their places (a blank draws an empty box), at
 * most BOARD_LABELS_MAX of BOARD_LABEL_MAX characters, and go when all are blank.
 */
export function stagingOf(s: StoredStaging): Staging {
  const out: Staging = {};
  if (CAMERAS.includes(s.camera as Camera)) out.camera = s.camera as Camera;
  if (LAYOUTS.includes(s.layout as StageLayout) && s.layout !== 'stage') out.layout = s.layout as StageLayout;
  const hero = phoneImage(s.screen);
  const more = (Array.isArray(s.screen?.more) ? s.screen.more : []).flatMap((p) => phoneImage(p) ?? []).slice(0, SCREEN_MORE_MAX);
  if (hero) out.screen = { ...hero, ...(more.length && { more }) };
  const badges = (Array.isArray(s.badges) ? s.badges : []).flatMap((b): Badge[] => {
    const text = str(b?.text);
    const tone = b?.tone;
    return text ? [{ text, tone: BADGE_TONES.includes(tone as BadgeTone) ? (tone as BadgeTone) : 'ink' }] : [];
  }).slice(0, BADGES_MAX);
  if (badges.length) out.badges = badges;
  const mark = [...str(s.mark).toUpperCase()].filter((c) => has(GLYPHS, c)).join('').slice(0, MARK_MAX);
  if (mark) out.mark = mark;
  if (HEADLINE_SPOTS.includes(s.headlineAt as HeadlineSpot)) out.headlineAt = s.headlineAt as HeadlineSpot;
  const labels = (Array.isArray(s.boardLabels) ? s.boardLabels : []).slice(0, BOARD_LABELS_MAX).map((l) => str(l).slice(0, BOARD_LABEL_MAX));
  if (labels.some(Boolean)) out.boardLabels = labels;
  return out;
}

/** Whether a shot draws one of the staging layouts rather than the character stage. */
export const staged = (shot: Pick<Shot, 'layout'>): boolean => !!shot.layout && shot.layout !== 'stage';

// ---------------------------------------------------------------- camera

/** Seconds of a framing move, bounded by the scene duration. */
export const CAMERA_S = 0.8;
export const ZOOM = 1.18;
const PULL_ZOOM = 1.12;
export interface CameraState { k: number }

/** Push and pull use independent framing and scene-local easing. */
export function cameraAt(camera: Camera | undefined, lt: number, endT?: number): CameraState {
  const duration = Math.min(CAMERA_S, endT === undefined ? CAMERA_S : Math.max(0, endT));
  if (duration <= 0) return { k: 1 };
  if (camera === 'push-in') {
    const p = prog(lt, endT === undefined ? 0 : Math.max(0, endT - duration), duration);
    return { k: 1 + (ZOOM - 1) * p * p * (3 - 2 * p) };
  }
  if (camera === 'pull-out') return { k: 1 + (PULL_ZOOM - 1) * (1 - ease.out(prog(lt, 0, duration))) };
  return { k: 1 };
}

export interface Focus { x: number; y: number }
export const focusOf = (W: number, H: number): Focus => ({ x: W / 2, y: H / 2 });
export interface CameraMove extends CameraState, Focus {}

/** Centre framing keeps the transformed backdrop covering all four frame edges. */
export function beginCamera(ctx: Ctx, shot: Shot, lt: number, timing: ShotTiming, W: number, H: number): CameraMove | null {
  const cam = cameraAt(shot.camera, lt, timing.endT);
  if (cam.k === 1) return null;
  const f = focusOf(W, H);
  // Keep bounded framing continuous; sprite drawing still samples its rig with nearest neighbours.
  const k = cam.k, tx = f.x * (1 - k), ty = f.y * (1 - k);
  ctx.save();
  ctx.translate(tx, ty);
  ctx.scale(k, k);
  cameraPixels(ctx, true);
  return { k, ...f };
}

export function endCamera(ctx: Ctx, move: CameraMove | null): void {
  if (move) {
    cameraPixels(ctx, false);
    ctx.restore();
  }
}

// ---------------------------------------------------------------- shared text

// The largest size up to `fs` at which `s` wraps into at most `lines` lines no wider than `maxW`.
function fit(ctx: Ctx, s: string, maxW: number, fs: number, weight: number, font: string, lines: number): { fs: number; lines: string[] } {
  for (let size = fs; ; size = Math.floor(size * 0.92)) {
    setFont(ctx, weight, size, font);
    const out = wrap(ctx, s, maxW);
    if (size <= 14 || (out.length <= lines && out.every((l) => ctx.measureText(l).width <= maxW))) return { fs: size, lines: out };
  }
}

// Comic-book lettering that reads on bright and dark backdrops alike: a hard shadow, a dark outline, a fill.
export function loud(ctx: Ctx, s: string, x: number, y: number, fs: number, color = WHITE): void {
  ctx.lineJoin = 'round';
  ctx.fillStyle = INK;
  paintText(ctx, s, x + fs * 0.05, y + fs * 0.07);
  ctx.lineWidth = fs * 0.12;
  ctx.strokeStyle = INK;
  paintText(ctx, s, x, y, true);
  ctx.fillStyle = color;
  paintText(ctx, s, x, y);
}

function hexRgb(hex: string): [number, number, number] {
  const n = parseInt(/^#[0-9a-f]{6}$/i.test(hex) ? hex.slice(1) : ACCENT.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const mix = (a: string, b: string, k: number): string =>
  '#' + hexRgb(a).map((v, i) => Math.round(v + (hexRgb(b)[i] - v) * k).toString(16).padStart(2, '0')).join('');

// Dark ink on a light colour, white on a dark one.
function inkOn(hex: string): string {
  const [r, g, b] = hexRgb(hex);
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? INK : '#ffffff';
}

// A stable 0..1 value per (seed, k): the placeholder screens vary by feature name, never by chance.
function seeded(seed: string, k: number): number {
  let h = 2166136261;
  for (const c of seed + ':' + k) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return ((h >>> 0) % 1000) / 1000;
}

// ---------------------------------------------------------------- screen

const PHONE_ASPECT = 0.49;

/** One phone at rest: its centre, size and tilt (radians, clockwise). */
export interface PhoneSlot {
  cx: number;
  cy: number;
  w: number;
  h: number;
  rot: number;
}

/**
 * Where a `screen` shot's phones and feature title (block left edge or centre, width, size) sit.
 * `phones` is phone 1, the hero (centre and in front), then phone 2 on its right and phone 3 on its left:
 * SIDE of its size, a little lower and tilted outward, like a hand of cards.
 */
export interface ScreenLayout {
  phones: PhoneSlot[];
  title: { x: number; cy: number; w: number; fs: number; center: boolean };
}

// The hero leans TILT. Phones 2 and 3 are SIDE of its size, SIDE_DROP of its height lower and FAN more tilted
// away from it; their centres sit `spread` of its width to either side.
const TILT = 0.04, SIDE = 0.88, SIDE_DROP = 0.06, FAN = 0.1;

// A rotated phone's half extents, its side buttons included.
function halfBox(p: PhoneSlot): [number, number] {
  const c = Math.abs(Math.cos(p.rot)), s = Math.abs(Math.sin(p.rot)), w = p.w * 1.04;
  return [(w * c + p.h * s) / 2, (w * s + p.h * c) / 2];
}

// n phones fanned about the hero, as large as fits the region [x0, y0, x1, y1] up to a hero hMax tall,
// centred in it.
function fan(n: number, spread: number, [x0, y0, x1, y1]: readonly number[], hMax: number): PhoneSlot[] {
  const unit = (cx: number, cy: number, h: number, rot: number): PhoneSlot => ({ cx, cy, h, w: h * PHONE_ASPECT, rot });
  const d = spread * PHONE_ASPECT;
  const slots = [unit(0, 0, 1, TILT), unit(d, SIDE_DROP, SIDE, TILT + FAN), unit(-d, SIDE_DROP, SIDE, TILT - FAN)].slice(0, n);
  const box = slots.reduce((b, p) => {
    const [hx, hy] = halfBox(p);
    return [Math.min(b[0], p.cx - hx), Math.min(b[1], p.cy - hy), Math.max(b[2], p.cx + hx), Math.max(b[3], p.cy + hy)];
  }, [Infinity, Infinity, -Infinity, -Infinity]);
  const k = Math.min(hMax, (x1 - x0) / (box[2] - box[0]), (y1 - y0) / (box[3] - box[1]));
  const ox = (x0 + x1 - (box[0] + box[2]) * k) / 2, oy = (y0 + y1 - (box[1] + box[3]) * k) / 2;
  return slots.map((p) => ({ cx: ox + p.cx * k, cy: oy + p.cy * k, w: p.w * k, h: p.h * k, rot: p.rot }));
}

/** A `screen` shot's layout in a W x H frame for n phones (1 to 1 + SCREEN_MORE_MAX). */
export function screenLayout(W: number, H: number, n = 1): ScreenLayout {
  const count = clamp(Math.floor(n), 1, 1 + SCREEN_MORE_MAX);
  const one = (cx: number, cy: number, h: number): PhoneSlot[] => [{ cx, cy, h, w: h * PHONE_ASPECT, rot: TILT }];
  if (W > H) {
    if (count === 1) return { phones: one(W * 0.7, H * 0.5, H * 0.84), title: { x: W * 0.07, cy: H * 0.5, w: W * 0.44, fs: 150, center: false } };
    return { phones: fan(count, count === 2 ? 0.8 : 0.72, [W * 0.44, H * 0.07, W * 0.97, H * 0.95], H * 0.8),
      title: { x: W * 0.06, cy: H * 0.5, w: W * 0.36, fs: 150, center: false } };
  }
  const title = { x: W * 0.07, cy: H * 0.18, w: W * 0.86, fs: W < H ? 140 : 110, center: true };
  if (count === 1) return { phones: W < H ? one(W / 2, H * 0.62, H * 0.6) : one(W / 2, H * 0.64, H * 0.62), title };
  return W < H
    ? { phones: fan(count, count === 2 ? 0.78 : 0.66, [W * 0.03, H * 0.26, W * 0.97, H * 0.95], H * 0.56), title }
    : { phones: fan(count, count === 2 ? 0.86 : 0.8, [W * 0.04, H * 0.3, W * 0.96, H * 0.98], H * 0.7), title };
}

/** The phones a `screen` shot draws: phone 1 (the generated app screen without a `screen`), then `more`. */
export function phonesOf(shot: Pick<Shot, 'screen'>): PhoneImage[] {
  const { more = [], ...hero } = shot.screen ?? {};
  return [hero, ...more].slice(0, 1 + SCREEN_MORE_MAX);
}

// Seconds from the scene's start: phone 1 springs up, the title slams down, the app fills in; phones 2
// and 3 spring up PHONE_STAGGER after the one before.
const PHONE_IN = 0.4, PHONE_STAGGER = 0.3, SLAM_T = 0.1, SLAM = 0.2, SHAKE = 0.3, LINES = 0.45;

function cover(ctx: Ctx, img: CanvasImageSource, w: number, h: number): void {
  const { width: iw, height: ih } = img as unknown as { width: number; height: number };
  if (!(iw > 0 && ih > 0)) return;
  const s = Math.max(w / iw, h / ih), dw = iw * s, dh = ih * s;
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(img, (w - dw) / 2, dh > h ? 0 : (h - dh) / 2, dw, dh);
}

// A generated app screen, w x h: the feature's header bar, a small chart, list rows with avatars and a
// tab bar, in the brand accent. The rows slide in and the bars grow.
function appScreen(ctx: Ctx, shot: Shot, lt: number, w: number, h: number, font: string, seed: string): void {
  const accent = shot.accent ?? ACCENT, ink = inkOn(accent), u = w / 100;
  ctx.fillStyle = '#f2f4f7';
  ctx.fillRect(0, 0, w, h);
  const head = h * 0.16;
  ctx.fillStyle = accent;
  ctx.fillRect(0, 0, w, head);
  setFont(ctx, 700, Math.round(u * 3.6), font);
  ctx.fillStyle = ink;
  ctx.textAlign = 'left';
  paintText(ctx, '9:41', u * 7, u * 7);
  ctx.fillRect(w - u * 13, u * 4.6, u * 6, u * 2.8);
  paintText(ctx, fit(ctx, shot.headline, w - u * 26, Math.round(u * 8), 800, font, 1).lines[0] ?? '', u * 6, head - u * 4.5);
  ctx.globalAlpha = 0.3;
  ctx.beginPath();
  ctx.arc(w - u * 10, head - u * 7, u * 4.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 1;

  const card = (x: number, y: number, cw: number, ch: number): void => {
    ctx.fillStyle = 'rgba(17,18,20,.06)';
    rr(ctx, x, y + u * 0.8, cw, ch, u * 3.5);
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    rr(ctx, x, y, cw, ch, u * 3.5);
    ctx.fill();
  };
  const cy = head + u * 5, ch = h * 0.2;
  card(u * 5, cy, w - u * 10, ch);
  ctx.fillStyle = '#c9d0d6';
  rr(ctx, u * 10, cy + u * 4, u * 30, u * 2.6, u * 1.3);
  ctx.fill();
  const bars = 7, bw = (w - u * 30) / bars;
  for (let i = 0; i < bars; i++) {
    const g = ease.out(prog(lt, 0.25 + i * 0.05, 0.35)), bh = (0.3 + 0.7 * seeded(seed, i)) * (ch - u * 16) * g;
    ctx.fillStyle = i === bars - 2 ? accent : mix(accent, '#ffffff', 0.45);
    rr(ctx, u * 10 + i * bw + bw * 0.15, cy + ch - u * 5 - bh, bw * 0.7, bh, [u * 1.2, u * 1.2, 0, 0]);
    ctx.fill();
  }

  const top = cy + ch + u * 5, rowH = h * 0.1, tabH = h * 0.08;
  for (let i = 0; top + (i + 1) * rowH <= h - tabH - u * 2; i++) {
    const x = u * 5 + (1 - ease.out(prog(lt, 0.2 + i * 0.07, 0.35))) * w, y = top + i * rowH;
    card(x, y, w - u * 10, rowH - u * 3);
    const my = y + (rowH - u * 3) / 2;
    ctx.fillStyle = mix(accent, i % 2 ? '#ffffff' : INK, i % 2 ? 0.35 : 0.15);
    ctx.beginPath();
    ctx.arc(x + u * 9, my, u * 4.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#aeb7bf';
    rr(ctx, x + u * 17, my - u * 3.2, u * (22 + 22 * seeded(seed, 10 + i)), u * 2.4, u * 1.2);
    ctx.fill();
    ctx.fillStyle = '#dde2e7';
    rr(ctx, x + u * 17, my + u * 1.2, u * (16 + 26 * seeded(seed, 20 + i)), u * 2, u * 1);
    ctx.fill();
    ctx.fillStyle = mix(accent, '#ffffff', 0.7);
    rr(ctx, x + w - u * 26, my - u * 2.6, u * 12, u * 5.2, u * 2.6);
    ctx.fill();
  }

  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, h - tabH, w, tabH);
  ctx.fillStyle = '#e3e7eb';
  ctx.fillRect(0, h - tabH, w, Math.max(1, u * 0.3));
  for (let i = 0; i < 4; i++) {
    ctx.fillStyle = i === 0 ? accent : '#c3cad1';
    rr(ctx, w * (0.125 + i * 0.25) - u * 3, h - tabH / 2 - u * 3.8, u * 6, u * 6, u * 1.8);
    ctx.fill();
  }
}

// A phone, centred on the origin: a rounded dark body with side buttons, an outline and a highlight, its
// screen (its host image `pic`, black while it loads, or the generated app screen seeded by `seed`), a
// glare and the notch.
function phone(ctx: Ctx, w: number, h: number, pic: PhoneImage, seed: string, shot: Shot, lt: number, env: MotionEnv, font: string): void {
  const r = w * 0.15, b = w * 0.045;
  ctx.save();
  shadow(ctx, 'rgba(0,0,0,.4)', w * 0.12, w * 0.04);
  ctx.fillStyle = '#16191d';
  rr(ctx, -w / 2, -h / 2, w, h, r);
  ctx.fill();
  ctx.restore();
  ctx.fillStyle = '#2a3037';
  for (const [x, y, bh] of [[-w / 2 - w * 0.016, -h * 0.24, h * 0.07], [-w / 2 - w * 0.016, -h * 0.14, h * 0.07], [w / 2 - w * 0.006, -h * 0.2, h * 0.11]]) {
    rr(ctx, x, y, w * 0.022, bh, w * 0.01);
    ctx.fill();
  }
  ctx.lineWidth = Math.max(2, w * 0.008);
  ctx.strokeStyle = '#3a424b';
  rr(ctx, -w / 2, -h / 2, w, h, r);
  ctx.stroke();
  const inset = w * 0.014;
  ctx.lineWidth = Math.max(1, w * 0.005);
  ctx.strokeStyle = 'rgba(255,255,255,.16)';
  rr(ctx, -w / 2 + inset, -h / 2 + inset, w - 2 * inset, h - 2 * inset, r - inset);
  ctx.stroke();

  const sx = -w / 2 + b, sy = -h / 2 + b, sw = w - 2 * b, sh = h - 2 * b;
  ctx.save();
  rr(ctx, sx, sy, sw, sh, r - b);
  ctx.clip();
  ctx.translate(sx, sy);
  const src = pic.image, img = src ? env.image?.(src) ?? null : null;
  if (img) cover(ctx, img, sw, sh);
  else if (src) {
    ctx.fillStyle = '#0d0f12';
    ctx.fillRect(0, 0, sw, sh);
  } else appScreen(ctx, shot, lt, sw, sh, font, seed);
  const glare = ctx.createLinearGradient(0, 0, sw, sh);
  glare.addColorStop(0, 'rgba(255,255,255,.14)');
  glare.addColorStop(0.4, 'rgba(255,255,255,0)');
  ctx.fillStyle = glare;
  ctx.fillRect(0, 0, sw, sh);
  ctx.restore();
  ctx.fillStyle = '#16191d';
  rr(ctx, -w * 0.15, sy + w * 0.03, w * 0.3, w * 0.075, w * 0.0375);
  ctx.fill();
}

// Speed lines bursting out of (x, y) on the slam's impact, `u` seconds after it: dark under white, so
// they read on the bright vision backdrop and on the dark ones.
function speedLines(ctx: Ctx, x: number, y: number, fs: number, u: number): void {
  const k = 1 - u / LINES;
  ctx.save();
  ctx.lineCap = 'round';
  ctx.globalAlpha = k;
  for (let i = 0; i < 24; i++) {
    const a = (i * 2 * Math.PI) / 24 + (i % 3) * 0.07, r0 = fs * (1.5 + u * 4), len = fs * (0.8 + (i % 4) * 0.35) * k;
    for (const [color, width] of [[INK, fs * 0.07], ['#ffffff', fs * 0.035]] as const) {
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.beginPath();
      ctx.moveTo(x + Math.cos(a) * r0, y + Math.sin(a) * r0 * 0.7);
      ctx.lineTo(x + Math.cos(a) * (r0 + len), y + Math.sin(a) * (r0 + len) * 0.7);
      ctx.stroke();
    }
  }
  ctx.restore();
}

function drawScreen(ctx: Ctx, shot: Shot, lt: number, W: number, H: number, env: MotionEnv): void {
  const pics = phonesOf(shot), { phones, title: T } = screenLayout(W, H, pics.length), font = fontOf(env);
  // Back to front, so phone 1 stands in front. Each springs up (swinging in from its side) once its turn
  // comes and bobs out of step with the others; a generated app screen fills in from its phone's entrance.
  for (let i = phones.length - 1; i >= 0; i--) {
    const P = phones[i], t = lt - i * PHONE_STAGGER, side = P.cx < phones[0].cx ? -1 : 1;
    if (t < 0) continue;
    const e = ease.back(prog(t, 0, PHONE_IN));
    ctx.save();
    ctx.translate(P.cx, P.cy + (1 - e) * H * 0.7 + Math.sin(lt * 2.4 + i * 1.9) * H * 0.005);
    ctx.rotate((1 - e) * 0.25 * side + P.rot);
    phone(ctx, P.w, P.h, pics[i], i ? `${shot.headline} ${i + 1}` : shot.headline, shot, t, env, font);
    ctx.restore();
  }

  const p = prog(lt, SLAM_T, SLAM);
  if (p <= 0) return;
  const { fs, lines } = fit(ctx, shot.headline, T.w, T.fs, 900, font, 2);
  const lh = fs * 1.02, top = T.cy - (lines.length * lh) / 2;
  const width = Math.max(0, ...lines.map((l) => ctx.measureText(l).width));
  const cx = T.center ? T.x + T.w / 2 : T.x + width / 2, u = lt - SLAM_T - SLAM;
  if (u >= 0 && u < LINES) speedLines(ctx, cx, T.cy, fs, u);
  const shake = u >= 0 && u < SHAKE ? Math.sin(u * 70) * (1 - u / SHAKE) * fs * 0.06 : 0, s = 1 + 1.4 * (1 - p) * (1 - p);
  ctx.save();
  ctx.globalAlpha = clamp(p * 2.5);
  ctx.translate(cx + shake, T.cy + shake * 0.6);
  ctx.rotate(-0.06);
  ctx.scale(s, s);
  ctx.translate(-cx, -T.cy);
  ctx.textAlign = T.center ? 'center' : 'left';
  const x = T.center ? cx : T.x;
  if (shot.eyebrow) {
    const fe = Math.round(fs * 0.26);
    setFont(ctx, 800, fe, font);
    const ew = ctx.measureText(shot.eyebrow).width, ex = T.center ? cx - ew / 2 : T.x, ey = top - fe * 1.1;
    ctx.fillStyle = INK;
    rr(ctx, ex - fe * 0.5, ey - fe * 1.05, ew + fe, fe * 1.5, fe * 0.75);
    ctx.fill();
    ctx.fillStyle = shot.accent ?? ACCENT;
    paintText(ctx, shot.eyebrow, x, ey);
  }
  setFont(ctx, 900, fs, font);
  lines.forEach((l, i) => loud(ctx, l, x, top + fs * 0.8 + i * lh, fs));
  ctx.restore();
  ctx.textAlign = 'left';
}

// ---------------------------------------------------------------- title

interface Tone {
  light: string;
  base: string;
  shade: string;
  ink: string;
}

// Fixed badge ramps; accent badges use the workspace colour.
const TONES: Readonly<Record<Exclude<BadgeTone, 'accent'>, Tone>> = {
  ink: { light: '#59616b', base: '#343b44', shade: '#20262d', ink: '#f4f6f7' },
  paper: { light: '#ffffff', base: '#eef2f4', shade: '#c9d2d8', ink: '#20262d' },
};

export const toneOf = (tone: BadgeTone, accent: string): Tone => tone === 'accent'
  ? { light: mix(accent, '#ffffff', 0.45), base: accent, shade: mix(accent, '#000000', 0.25), ink: inkOn(accent) }
  : TONES[tone];

/** Where a `title` card sits: centred on (W/2, cy), the badges' and the headline's type sizes and width. */
export function titleLayout(W: number, H: number): { cy: number; fb: number; fs: number; maxW: number; stack: boolean } {
  if (W > H) return { cy: H * 0.47, fb: 110, fs: 150, maxW: W * 0.84, stack: false };
  if (W < H) return { cy: H * 0.45, fb: 96, fs: 124, maxW: W * 0.88, stack: true };
  return { cy: H * 0.47, fb: 90, fs: 112, maxW: W * 0.88, stack: false };
}

// Seconds from the scene's start: badge i drops at BADGE_T + i * STAGGER, bouncing to rest in DROP.
const BADGE_T = 0.2, STAGGER = 0.25, DROP = 0.7, SHEEN = 0.6;

// How far a dropped badge has fallen (1 = at rest): a fall, then two shrinking bounces.
function bounce(p: number): number {
  if (p < 0.5) return (p / 0.5) ** 2;
  if (p < 0.78) {
    const u = (p - 0.5) / 0.28;
    return 1 - 0.56 * u * (1 - u);
  }
  const u = (p - 0.78) / 0.22;
  return 1 - 0.16 * u * (1 - u);
}

const squash = (p: number): number => Math.max(0, 1 - Math.abs(p - 0.5) / 0.05) * 0.12 + Math.max(0, 1 - Math.abs(p - 0.78) / 0.04) * 0.05;

export function badge(ctx: Ctx, text: string, w: number, h: number, fb: number, tone: Tone, font: string, sheen: number | null): void {
  const r = h * 0.24, o = Math.max(3, Math.round(h * 0.06));
  ctx.fillStyle = 'rgba(0,0,0,.45)';
  rr(ctx, -w / 2 + o, -h / 2 + o * 1.6, w, h, r);
  ctx.fill();
  ctx.fillStyle = INK;
  rr(ctx, -w / 2, -h / 2, w, h, r);
  ctx.fill();
  const plate = ctx.createLinearGradient(0, -h / 2, 0, h / 2);
  plate.addColorStop(0, tone.light);
  plate.addColorStop(0.45, tone.base);
  plate.addColorStop(1, tone.shade);
  ctx.save();
  rr(ctx, -w / 2 + o, -h / 2 + o, w - 2 * o, h - 2 * o, r - o);
  ctx.fillStyle = plate;
  ctx.fill();
  ctx.clip();
  ctx.fillStyle = tone.light;
  ctx.fillRect(-w / 2 + r, -h / 2 + o * 1.8, w - 2 * r, o);
  if (sheen !== null) {
    const x = -w / 2 - h + (w + 2 * h) * sheen;
    ctx.fillStyle = 'rgba(255,255,255,.55)';
    ctx.beginPath();
    ctx.moveTo(x, h / 2);
    ctx.lineTo(x + h * 0.5, -h / 2);
    ctx.lineTo(x + h * 0.85, -h / 2);
    ctx.lineTo(x + h * 0.35, h / 2);
    ctx.fill();
  }
  ctx.restore();
  setFont(ctx, 900, fb, font);
  ctx.textAlign = 'center';
  ctx.fillStyle = tone.ink;
  paintText(ctx, text, 0, fb * 0.36);
  ctx.textAlign = 'left';
}

// A badge `w` x `bh` dropping from above the frame onto (restX, restY) from `t0`: it falls, bounces twice and
// squashes on each landing, kicks up dust and catches a sheen once at rest.
function dropBadge(
  ctx: Ctx, b: Badge, w: number, bh: number, fb: number, restX: number, restY: number, t0: number, lt: number, accent: string, font: string,
): void {
  const p = prog(lt, t0, DROP);
  if (lt < t0) return;
  const y = restY - (1 - bounce(p)) * (restY + bh), sq = squash(p), tone = toneOf(b.tone, accent);
  const landed = lt - t0 - DROP * 0.5;
  ctx.save();
  ctx.translate(restX, y + bh / 2);
  if (landed >= 0 && landed < 0.35) dust(ctx, w, bh, landed, tone.light);
  ctx.scale(1 + sq, 1 - sq);
  ctx.translate(0, -bh / 2);
  const since = lt - t0 - DROP;
  badge(ctx, b.text, w, bh, fb, tone, font, since >= 0 && since < SHEEN ? since / SHEEN : null);
  ctx.restore();
}

// Pixel puffs kicked out from a badge's feet as it lands, u seconds after.
function dust(ctx: Ctx, w: number, h: number, u: number, color: string): void {
  const k = u / 0.35, q = Math.max(3, Math.round(h * 0.07));
  ctx.save();
  ctx.globalAlpha = 1 - k;
  ctx.fillStyle = color;
  for (let i = 0; i < 6; i++) {
    const side = i % 2 ? 1 : -1, d = (0.2 + 0.8 * ease.out(k)) * h * (0.6 + (i >> 1) * 0.35);
    ctx.fillRect(Math.round(side * (w / 2 + d) - q / 2), Math.round(-q - (i >> 1) * q * 1.4 - k * h * 0.3), q, q);
  }
  ctx.restore();
}

function drawTitle(ctx: Ctx, shot: Shot, lt: number, W: number, H: number, env: MotionEnv): void {
  const T = titleLayout(W, H), font = fontOf(env), badges = shot.badges ?? [], accent = shot.accent ?? ACCENT;
  let fb = T.fb;
  const measure = (): { ws: number[]; bh: number; gap: number } => {
    setFont(ctx, 900, fb, font);
    return { ws: badges.map((b) => ctx.measureText(b.text).width + fb * 1.4), bh: fb * 1.6, gap: fb * 0.45 };
  };
  let m = measure();
  const rowW = (): number => m.ws.reduce((a, b) => a + b, 0) + m.gap * Math.max(0, badges.length - 1);
  const shrink = (floor: number, tooWide: () => boolean): void => {
    while (fb > floor && tooWide()) {
      fb = Math.floor(fb * 0.92);
      m = measure();
    }
  };
  // Side by side while the badges keep most of their size; a tall frame stacks them rather than shrink further.
  shrink(T.fb * 0.7, () => rowW() > T.maxW);
  const stack = T.stack && rowW() > T.maxW;
  shrink(20, () => (stack ? Math.max(0, ...m.ws) : rowW()) > T.maxW);
  const { fs, lines } = fit(ctx, shot.headline, T.maxW, T.fs, 900, font, 2);
  const badgesH = badges.length ? (stack ? badges.length * m.bh + (badges.length - 1) * m.gap : m.bh) : 0;
  const lh = fs * 1.05, gap = badges.length ? fb * 0.9 : 0, blockH = badgesH + gap + lines.length * lh;
  const top = T.cy - blockH / 2;

  const xs = badges.map((_, i) => W / 2 - rowW() / 2 + m.ws.slice(0, i).reduce((a, b) => a + b + m.gap, 0) + m.ws[i] / 2);
  // A stacked badge falls behind the ones above it.
  (stack ? [...badges.keys()].reverse() : [...badges.keys()]).forEach((i) => {
    const restX = stack ? W / 2 : xs[i], restY = top + (stack ? i * (m.bh + m.gap) : 0) + m.bh / 2;
    dropBadge(ctx, badges[i], m.ws[i], m.bh, fb, restX, restY, BADGE_T + i * STAGGER, lt, accent, font);
  });

  const tH = badges.length ? BADGE_T + (badges.length - 1) * STAGGER + DROP * 0.8 : 0.3;
  const p = ease.out(prog(lt, tH, 0.5));
  if (p <= 0) return;
  ctx.save();
  ctx.globalAlpha = p;
  ctx.textAlign = 'center';
  setFont(ctx, 900, fs, font);
  const y0 = top + badgesH + gap + fs * 0.82 + (1 - p) * fs * 0.5;
  lines.forEach((l, i) => loud(ctx, l, W / 2, y0 + i * lh, fs));
  ctx.restore();
  ctx.textAlign = 'left';
}

// ---------------------------------------------------------------- signoff

// A 5 x 7 pixel face for the mark's letters: one string per row, `#` lit.
const GLYPHS: Readonly<Record<string, readonly string[]>> = {
  A: ['.###.', '#...#', '#...#', '#####', '#...#', '#...#', '#...#'],
  B: ['####.', '#...#', '#...#', '####.', '#...#', '#...#', '####.'],
  C: ['.###.', '#...#', '#....', '#....', '#....', '#...#', '.###.'],
  D: ['####.', '#...#', '#...#', '#...#', '#...#', '#...#', '####.'],
  E: ['#####', '#....', '#....', '####.', '#....', '#....', '#####'],
  F: ['#####', '#....', '#....', '####.', '#....', '#....', '#....'],
  G: ['.###.', '#...#', '#....', '#.###', '#...#', '#...#', '.####'],
  H: ['#...#', '#...#', '#...#', '#####', '#...#', '#...#', '#...#'],
  I: ['#####', '..#..', '..#..', '..#..', '..#..', '..#..', '#####'],
  J: ['..###', '...#.', '...#.', '...#.', '...#.', '#..#.', '.##..'],
  K: ['#...#', '#..#.', '#.#..', '##...', '#.#..', '#..#.', '#...#'],
  L: ['#....', '#....', '#....', '#....', '#....', '#....', '#####'],
  M: ['#...#', '##.##', '#.#.#', '#.#.#', '#...#', '#...#', '#...#'],
  N: ['#...#', '#...#', '##..#', '#.#.#', '#..##', '#...#', '#...#'],
  O: ['.###.', '#...#', '#...#', '#...#', '#...#', '#...#', '.###.'],
  P: ['####.', '#...#', '#...#', '####.', '#....', '#....', '#....'],
  Q: ['.###.', '#...#', '#...#', '#...#', '#.#.#', '#..#.', '.##.#'],
  R: ['####.', '#...#', '#...#', '####.', '#.#..', '#..#.', '#...#'],
  S: ['.####', '#....', '#....', '.###.', '....#', '....#', '####.'],
  T: ['#####', '..#..', '..#..', '..#..', '..#..', '..#..', '..#..'],
  U: ['#...#', '#...#', '#...#', '#...#', '#...#', '#...#', '.###.'],
  V: ['#...#', '#...#', '#...#', '#...#', '#...#', '.#.#.', '..#..'],
  W: ['#...#', '#...#', '#...#', '#.#.#', '#.#.#', '#.#.#', '.#.#.'],
  X: ['#...#', '#...#', '.#.#.', '..#..', '.#.#.', '#...#', '#...#'],
  Y: ['#...#', '#...#', '.#.#.', '..#..', '..#..', '..#..', '..#..'],
  Z: ['#####', '....#', '...#.', '..#..', '.#...', '#....', '#####'],
  0: ['.###.', '#...#', '#..##', '#.#.#', '##..#', '#...#', '.###.'],
  1: ['..#..', '.##..', '..#..', '..#..', '..#..', '..#..', '.###.'],
  2: ['.###.', '#...#', '....#', '...#.', '..#..', '.#...', '#####'],
  3: ['####.', '....#', '....#', '.###.', '....#', '....#', '####.'],
  4: ['...#.', '..##.', '.#.#.', '#..#.', '#####', '...#.', '...#.'],
  5: ['#####', '#....', '####.', '....#', '....#', '#...#', '.###.'],
  6: ['..##.', '.#...', '#....', '####.', '#...#', '#...#', '.###.'],
  7: ['#####', '....#', '...#.', '..#..', '.#...', '.#...', '.#...'],
  8: ['.###.', '#...#', '#...#', '.###.', '#...#', '#...#', '.###.'],
  9: ['.###.', '#...#', '#...#', '.####', '....#', '...#.', '.##..'],
  '&': ['.##..', '#..#.', '#.#..', '.#...', '#.#.#', '#..#.', '.##.#'],
};

/** The glyphs a mark can use: A–Z, 0–9 and &. */
export const MARK_CHARS = Object.keys(GLYPHS).join('');

const glyphsOf = (mark: string): (readonly string[])[] => [...mark].flatMap((c) => (has(GLYPHS, c) ? [GLYPHS[c]] : []));

/** Glyph cells across a mark: five per letter and one between letters. */
const cellsOf = (mark: string): number => Math.max(0, glyphsOf(mark).length * 6 - 1);

/**
 * Where a sign-off sits: its text block, the ground, the scene's pixel size `g` (the performer's rig
 * pixel, which the letters share), `c` rig pixels per glyph cell, the letters' left edge (their cells;
 * the outline is one pixel outside), and the performer's feet and height.
 */
export interface SignoffLayout {
  text: { x: number; cy: number; w: number; fs: number; center: boolean };
  ground: number;
  g: number;
  c: number;
  lx: number;
  px: number;
  h: number;
}

// The letters stand this tall beside the performer, in its rows.
const MARK_TALL = 0.66;

export function signoffLayout(W: number, H: number, rig: Character, mark = MARK): SignoffLayout {
  const land = W > H, tall = W < H;
  const text = land ? { x: W * 0.07, cy: H * 0.45, w: W * 0.4, fs: 150, center: false }
    : { x: W * 0.07, cy: H * (tall ? 0.19 : 0.17), w: W * 0.86, fs: tall ? 110 : 96, center: true };
  const [x0, x1] = land ? [W * 0.49, W * 0.96] : [W * 0.06, W * 0.94];
  const rows = rig.rows ?? 50, cells = cellsOf(mark), c = Math.max(2, Math.round((MARK_TALL * rows) / 7));
  const size = (g: number) => {
    const h = g * rows, hw = ((rig.aspect ?? 0.53) * h) / 2, reach = hw + 0.12 * h;
    return { h, reach, total: (cells * c + 2) * g + reach + hw };
  };
  let g = Math.max(1, Math.floor((H * (land ? 0.56 : tall ? 0.32 : 0.44)) / rows));
  while (g > 1 && size(g).total > x1 - x0) g--;
  const s = size(g), lx = Math.round((x0 + x1) / 2 - s.total / 2) + g;
  return { text, ground: Math.round(H * (land ? 0.82 : tall ? 0.8 : 0.88)), g, c, lx, px: Math.round(lx + (cells * c + 1) * g + s.reach), h: s.h };
}

// One mark canvas per env and size; each paint clears it fully, so reuse stays pure.
const markCanvases = new WeakMap<MotionEnv, Map<string, RigCanvas>>();

// The mark at one canvas pixel per rig pixel, in the house style: each glyph cell a c x c block of the
// shot accent, cel shaded as lit from above and in front (a highlight on top edges, a shade over the
// bottom edges and down the right ones, the deep shade under), ringed by a 1px outline. Letter i's cells
// start at x = 1 + 6ic, row 1.
function paintMark(env: MotionEnv, mark: string, c: number, accent: string): { canvas: RigCanvas; w: number; h: number } {
  const ramp = { H: mix(accent, '#ffffff', 0.45), B: accent, D: mix(accent, '#000000', 0.25), E: mix(accent, '#000000', 0.55) };
  const glyphs = glyphsOf(mark), w = cellsOf(mark) * c + 2, h = 7 * c + 2;
  let sized = markCanvases.get(env);
  if (!sized) markCanvases.set(env, (sized = new Map()));
  let canvas = sized.get(`${w}x${h}`);
  if (!canvas) sized.set(`${w}x${h}`, (canvas = env.createCanvas(w, h)));
  const oc = canvas.getContext('2d');
  if (!oc) throw new Error('motion-core: env.createCanvas returned a canvas without a 2d context');
  oc.clearRect(0, 0, w, h);
  const solid = (x: number, y: number): boolean => {
    if (x < 1 || y < 1 || y >= 1 + 7 * c) return false;
    const col = Math.floor((x - 1) / c), i = Math.floor(col / 6), k = col % 6;
    return i < glyphs.length && k < 5 && glyphs[i][Math.floor((y - 1) / c)][k] === '#';
  };
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let tone: string | null = null;
    if (solid(x, y)) {
      tone = !solid(x, y - 1) ? ramp.H : !solid(x, y + 1) ? ramp.E
        : !solid(x, y + 2) || !solid(x + 1, y) ? ramp.D : ramp.B;
    } else if ([-1, 0, 1].some((dy) => [-1, 0, 1].some((dx) => solid(x + dx, y + dy)))) tone = INK;
    if (tone) {
      oc.fillStyle = tone;
      oc.fillRect(x, y, 1, 1);
    }
  }
  return { canvas, w, h };
}

/**
 * The sign-off's pixel letters: `mark` at the scene's pixel size `g`, `c` rig
 * pixels per glyph cell, the outline under them standing on `by` and their cells starting at `x`.
 * Letter i pops up out of the ground to `pops(i)` of its height (0 hidden, 1 at rest; ease.back
 * overshoots into a stretch).
 */
export function drawMark(
  ctx: Ctx, mark: string, x: number, by: number, g: number, c: number, env: MotionEnv, accent = ACCENT, pops: (i: number) => number = () => 1,
): void {
  const { canvas, h } = paintMark(env, mark, c, accent);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  glyphsOf(mark).forEach((_, i) => {
    const p = pops(i);
    if (p <= 0) return;
    ctx.save();
    ctx.translate(Math.round(x) + (i * 6 * c - 1) * g, Math.round(by));
    ctx.scale(1, p);
    ctx.drawImage(canvas, i * 6 * c, 0, 5 * c + 2, h, 0, -h * g, (5 * c + 2) * g, h * g);
    ctx.restore();
  });
  ctx.restore();
}

// Seconds from the scene start: letters pop up and text rises beside the resting performer.
const MARK_T = 0.05, MARK_STAGGER = 0.09, POP = 0.4, EYEBROW_T = 0.3, HEADLINE_T = 0.45;

function drawSignoff(ctx: Ctx, shot: Shot, lt: number, W: number, H: number, env: MotionEnv): void {
  const rig = characterOf(shot.character), mark = shot.mark ?? MARK, S = signoffLayout(W, H, rig, mark), font = fontOf(env);
  if (cellsOf(mark)) {
    const wide = cellsOf(mark) * S.c * S.g;
    ctx.fillStyle = 'rgba(0,0,0,.28)';
    ctx.beginPath();
    ctx.ellipse(S.lx + wide / 2, S.ground, wide / 2 + 2 * S.g, 2 * S.g, 0, 0, Math.PI * 2);
    ctx.fill();
    drawMark(ctx, mark, S.lx, S.ground, S.g, S.c, env, shot.accent ?? ACCENT, (i) => ease.back(prog(lt, MARK_T + i * MARK_STAGGER, POP)));
  }

  ctx.save();
  ctx.translate(S.px, S.ground);
  drawCharacter(ctx, 0, 0, S.h, performer(shot, lt), env, false);
  ctx.restore();
  drawMissingCharacter(ctx, S.px, S.ground, S.h, shot.character, env);

  const T = S.text;
  const { fs, lines } = fit(ctx, shot.headline, T.w, T.fs, 800, font, 2);
  // At least 40% of the headline's own (post-fit) size, so it stays legible beside it in every format.
  const fe = Math.round(fs * 0.4);
  const lh = fs * 1.05, blockH = (shot.eyebrow ? fe * 1.6 : 0) + lines.length * lh, top = T.cy - blockH / 2;
  const x = T.center ? T.x + T.w / 2 : T.x;
  ctx.save();
  ctx.textAlign = T.center ? 'center' : 'left';
  const rise = (t0: number): number => {
    const p = ease.out(prog(lt, t0, 0.5));
    ctx.globalAlpha = p;
    return (1 - p) * fs * 0.4;
  };
  if (shot.eyebrow) {
    const dy = rise(EYEBROW_T);
    setFont(ctx, 800, fe, font);
    ctx.fillStyle = shot.accent ?? ACCENT;
    paintText(ctx, shot.eyebrow, x, top + fe + dy);
  }
  const dy = rise(HEADLINE_T);
  setFont(ctx, 800, fs, font);
  ctx.fillStyle = WHITE;
  lines.forEach((l, i) => paintText(ctx, l, x, top + (shot.eyebrow ? fe * 1.6 : 0) + fs * 0.85 + i * lh + dy));
  ctx.restore();
}

// ---------------------------------------------------------------- stage labels

// Shares of the scene: the headline over the co-star fades in at LABEL_HEADLINE, the badges start dropping
// under the ensemble at LABEL_BADGES. A scene of LABEL_SCENE seconds when the host names no length (a tile).
export const LABEL_HEADLINE = 0.25, LABEL_BADGES = 0.45;
const LABEL_SCENE = 4, LABEL_FADE = 0.6;

/**
 * A duo's or an ensemble's performers left to right at their settled spots, which one is the co-star, the members,
 * each performer's bubble and the Brief card's scale (its layout's diagram box width).
 */
interface StageRow {
  spots: (DuoSlot & BoardSpot)[];
  costar: number | null;
  members: number[];
  bubbles: (BubbleId | undefined)[];
}

function stageRow(shot: Shot, W: number, H: number): StageRow | null {
  if (staged(shot)) return null;
  const lead = characterOf(shot.character);
  if (shot.ensemble?.length) {
    const others = [shot.costar, ...shot.ensemble.slice(0, ENSEMBLE_MAX)].flatMap((m) => (m ? [m] : []));
    const rigs = [lead, ...others.map((m) => characterOf(m.character))], E = ensembleLayout(W, H, rigs);
    return { spots: E.slots.map((s, i) => ({ ...s, rig: rigs[i] })), costar: shot.costar ? 1 : null, members: memberSlots(shot),
      bubbles: [shot.bubble, ...others.map((m) => m.bubble)] };
  }
  if (!shot.costar) return null;
  const rig = characterOf(shot.costar.character), D = duoLayout(W, H, lead, rig, (shot.interaction ?? 'none') !== 'none');
  return { spots: [{ ...D.lead, rig: lead }, { ...D.costar, rig }], costar: 1, members: [], bubbles: [shot.bubble, shot.costar.bubble] };
}

/** The headline over the co-star: its lines, centred on `cx`, the first baseline at `y`, `fs` pixels. */
export interface CostarHeadline {
  cx: number;
  y: number;
  fs: number;
  lines: string[];
}

/** The badges under the ensemble: each one's text, tone and resting centre, `w` x `bh`, lettered at `fb`. */
export interface StageBadges {
  fb: number;
  bh: number;
  items: { badge: Badge; x: number; y: number; w: number }[];
}

/** Where a stage shot's labels sit, when it has them, and the independent diagram the headline keeps clear of. */
export interface StageLabels {
  headline: CostarHeadline | null;
  badges: StageBadges | null;
  board: BoardRect | null;
}

/**
 * Where a stage shot's labels go in a W x H frame. The headline (`headlineAt: costar`) sits over the
 * co-star, as large as fits beside the independent diagram or above it, clear of every head and every bubble the
 * shot draws. The badges stand
 * under the ensemble's members, one each, shrunk until the row fits and nudged apart where they would touch.
 */
export function stageLabels(ctx: Ctx, shot: Shot, W: number, H: number, env: MotionEnv): StageLabels {
  const row = stageRow(shot, W, H), out: StageLabels = { headline: null, badges: null, board: null };
  if (!row) return out;
  const font = fontOf(env), m = Math.round(Math.min(W, H) * 0.04), gap = Math.round(Math.min(W, H) * 0.02);
  out.board = isBoard(shot.diagram) ? boardRect(W, H, { spots: row.spots, bubbles: row.bubbles, over: row.members, top: 0 }) : null;
  const board = out.board;
  if (overCostar(shot) && row.costar !== null && !shot.hideHeadline) {
    const c = row.spots[row.costar];
    const bubbles = row.spots.flatMap((sp, i) => {
      const kind = row.bubbles[i];
      return kind ? [bubbleLayout(kind, sp, sp.rig, W, H).body] : [];
    });
    // The lowest the text may reach over [x0, x1]: above every head and bubble there.
    const ceiling = (x0: number, x1: number): number => Math.min(
      ...row.spots.filter((sp) => sp.x + widthOf(sp.rig, sp.h) / 2 > x0 && sp.x - widthOf(sp.rig, sp.h) / 2 < x1).map((sp) => sp.by - sp.h),
      ...bubbles.filter((b) => b.x + b.w > x0 && b.x < x1).map((b) => b.y),
      H - m,
    ) - gap;
    const beside: [number, number] = !board ? [m, W - m] : c.x < board.x + board.w / 2 ? [m, board.x - gap] : [board.x + board.w + gap, W - m];
    const regions = [
      { x0: beside[0], x1: beside[1], y1: ceiling(beside[0], beside[1]) },
      ...(board ? [{ x0: m, x1: W - m, y1: Math.min(board.y - gap, ceiling(m, W - m)) }] : []),
    ];
    const cap = LABEL_FS[W > H ? 'land' : W < H ? 'tall' : 'square'];
    const fits = regions.map((g): CostarHeadline => {
      let fs: number = cap, lines: string[] = [];
      for (;;) {
        ({ fs, lines } = fit(ctx, shot.headline, g.x1 - g.x0, fs, 900, font, 2));
        if (fs <= 14 || lines.length * fs * LABEL_LINE <= g.y1 - m) break;
        fs = Math.floor(fs * 0.92);
      }
      setFont(ctx, 900, fs, font);
      const w = Math.max(0, ...lines.map((l) => ctx.measureText(l).width));
      return { cx: clamp(c.x, g.x0 + w / 2, g.x1 - w / 2), y: labelTop(g.y1, lines.length, fs), fs, lines };
    });
    out.headline = fits.reduce((a, b) => (b.fs > a.fs ? b : a));
  }
  const members = row.members.slice(0, shot.badges?.length ?? 0), badges = shot.badges ?? [];
  if (members.length) {
    const by = row.spots[members[0]].by, top = by + Math.max(gap, (H - by) * 0.12), g = Math.round(Math.min(W, H) * 0.015);
    let fb = Math.floor(Math.min(titleLayout(W, H).fb * 0.7, (H - m / 2 - top) / 1.7));
    const widths = (): number[] => {
      setFont(ctx, 900, fb, font);
      return members.map((_, i) => ctx.measureText(badges[i].text).width + fb * 1.4);
    };
    let ws = widths();
    while (fb > 12 && ws.reduce((a, b) => a + b, 0) + g * (ws.length - 1) > W - 2 * m) {
      fb = Math.floor(fb * 0.92);
      ws = widths();
    }
    // Under each member, pushed apart where two would touch and back inside the frame.
    const xs = members.map((i) => row.spots[i].x), n = xs.length, apart = (i: number) => (ws[i - 1] + ws[i]) / 2 + g;
    for (let i = 1; i < n; i++) xs[i] = Math.max(xs[i], xs[i - 1] + apart(i));
    xs[n - 1] = Math.min(xs[n - 1], W - m - ws[n - 1] / 2);
    for (let i = n - 2; i >= 0; i--) xs[i] = Math.min(xs[i], xs[i + 1] - apart(i + 1));
    xs[0] = Math.max(xs[0], m + ws[0] / 2);
    for (let i = 1; i < n; i++) xs[i] = Math.max(xs[i], xs[i - 1] + apart(i));
    const bh = fb * 1.6;
    out.badges = { fb, bh, items: members.map((_, i) => ({ badge: badges[i], x: xs[i], y: top + bh / 2, w: ws[i] })) };
  }
  return out;
}

/**
 * A duo's or an ensemble's labels at `lt`, over the stage: the headline over the co-star fading in
 * LABEL_HEADLINE into the scene, then the badges dropping under the members one after another from
 * LABEL_BADGES, with the title card's drop, bounce, dust and sheen. Nothing for a shot without them.
 */
export function drawStageLabels(ctx: Ctx, shot: Shot, lt: number, timing: ShotTiming, W: number, H: number, env: MotionEnv): void {
  if (!overCostar(shot) && !(shot.badges?.length && shot.ensemble?.length)) return;
  const L = stageLabels(ctx, shot, W, H, env), dur = timing.endT ?? LABEL_SCENE, font = fontOf(env), accent = shot.accent ?? ACCENT;
  ctx.save();
  ctx.textBaseline = 'alphabetic';
  const h = L.headline, p = h ? ease.out(prog(lt, dur * LABEL_HEADLINE, LABEL_FADE)) : 0;
  if (h && p > 0) {
    ctx.save();
    ctx.globalAlpha = p;
    ctx.textAlign = 'center';
    setFont(ctx, 900, h.fs, font);
    h.lines.forEach((l, i) => loud(ctx, l, h.cx, h.y + i * h.fs * 1.05 + (1 - p) * h.fs * 0.3, h.fs));
    ctx.restore();
  }
  const B = L.badges;
  B?.items.forEach(({ badge: b, x, y, w }, i) => dropBadge(ctx, b, w, B.bh, B.fb, x, y, dur * LABEL_BADGES + i * STAGGER, lt, accent, font));
  ctx.restore();
}

// ---------------------------------------------------------------- dispatch

/** A staged shot (`screen`, `title` or `signoff`) at scene-local time `lt`, in W x H space, over its backdrop. */
export function drawLayout(ctx: Ctx, shot: Shot, lt: number, W: number, H: number, env: MotionEnv): void {
  ctx.save();
  ctx.textBaseline = 'alphabetic';
  ctx.textAlign = 'left';
  backdrop(ctx, shot.backdrop, W, H, lt);
  if (shot.layout === 'screen') drawScreen(ctx, shot, lt, W, H, env);
  else if (shot.layout === 'title') drawTitle(ctx, shot, lt, W, H, env);
  else if (shot.layout === 'signoff') drawSignoff(ctx, shot, lt, W, H, env);
  ctx.restore();
}
