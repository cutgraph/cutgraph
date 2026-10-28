import type { Ctx, FontEnv, MotionEnv, RigCanvas } from './types.ts';

export const DEFAULT_FONT = '"Inter"';

export const fontOf = (env: FontEnv | undefined): string => env?.fontFamily ?? DEFAULT_FONT;

export const clamp = (v: number, a = 0, b = 1): number => Math.min(b, Math.max(a, v));

export const prog = (t: number, start: number, dur: number): number => clamp((t - start) / Math.max(dur, 0.001));

export const ease = {
  out: (t: number): number => 1 - Math.pow(1 - t, 3),
  back: (t: number): number =>
    t <= 0 ? 0 : t >= 1 ? 1 : 1 + 2.70158 * Math.pow(t - 1, 3) + 1.70158 * Math.pow(t - 1, 2),
};

// Canvas shadows are sized in device pixels whatever the transform, so `atScale` records its scale for `shadow`.
const SHADOW_SCALE = new WeakMap<Ctx, number>();
const DESIGN_TRANSFORM = new WeakMap<Ctx, DOMMatrix>();
const CAMERA_PIXELS = new WeakSet<Ctx>();
const PIXEL_LAYERS = new WeakMap<MotionEnv, { canvas: RigCanvas; width: number; height: number }>();
const LAYER_STEP = 256;

export function cameraPixels(ctx: Ctx, active: boolean): void {
  if (active) CAMERA_PIXELS.add(ctx);
  else CAMERA_PIXELS.delete(ctx);
}

/** Sample zoomed sprites on the design grid before enlarging; text and vector edges keep export detail. */
export function pixelImage(ctx: Ctx, image: RigCanvas, x: number, y: number, w: number, h: number, env: MotionEnv): void {
  const base = DESIGN_TRANSFORM.get(ctx);
  if (!base || !CAMERA_PIXELS.has(ctx)) {
    ctx.drawImage(image, x, y, w, h);
    return;
  }
  const m = base.inverse().multiply(ctx.getTransform());
  const corners = [[x, y], [x + w, y], [x, y + h], [x + w, y + h]];
  const xs = corners.map(([px, py]) => m.a * px + m.c * py + m.e);
  const ys = corners.map(([px, py]) => m.b * px + m.d * py + m.f);
  const left = Math.floor(Math.min(...xs)), top = Math.floor(Math.min(...ys));
  const width = Math.ceil(Math.max(...xs)) - left, height = Math.ceil(Math.max(...ys)) - top;
  // Performers of different sizes and a zoom that grows every frame share one layer, which only grows, in
  // LAYER_STEP steps. Each use clears all of it, so nothing from an earlier performer or frame can show
  // through. It is drawn whole, clipped to the sprite's box: on a scaled host a source rectangle samples
  // differently, and the clip keeps the box's edge coverage.
  let layer = PIXEL_LAYERS.get(env);
  if (!layer || layer.width < width || layer.height < height) {
    const step = (v: number, had = 0): number => Math.max(had, Math.ceil(v / LAYER_STEP) * LAYER_STEP);
    const lw = step(width, layer?.width), lh = step(height, layer?.height);
    layer = { canvas: env.createCanvas(lw, lh), width: lw, height: lh };
    PIXEL_LAYERS.set(env, layer);
  }
  const oc = layer.canvas.getContext('2d');
  if (!oc) throw new Error('Pixel layer needs a 2d context');
  oc.setTransform(1, 0, 0, 1, 0, 0);
  oc.clearRect(0, 0, layer.width, layer.height);
  oc.setTransform(m.a, m.b, m.c, m.d, m.e - left, m.f - top);
  oc.imageSmoothingEnabled = false;
  oc.drawImage(image, x, y, w, h);
  ctx.save();
  ctx.setTransform(base.a, base.b, base.c, base.d, base.e, base.f);
  ctx.imageSmoothingEnabled = false;
  ctx.beginPath();
  ctx.rect(left, top, width, height);
  ctx.clip();
  ctx.drawImage(layer.canvas, left, top);
  ctx.restore();
}

/** Runs `paint` with the drawing scaled by `k` device pixels per design pixel, shadows included. */
export function atScale(ctx: Ctx, k: number, paint: () => void): void {
  ctx.save();
  ctx.scale(k, k);
  SHADOW_SCALE.set(ctx, k);
  // Include the host's transform so camera sampling stays on the design grid in a scaled preview.
  DESIGN_TRANSFORM.set(ctx, ctx.getTransform());
  try {
    paint();
  } finally {
    SHADOW_SCALE.delete(ctx);
    DESIGN_TRANSFORM.delete(ctx);
    ctx.restore();
  }
}

/** Sets a shadow in design pixels; `offsetY` only when given. */
export function shadow(ctx: Ctx, color: string, blur: number, offsetY?: number): void {
  const k = SHADOW_SCALE.get(ctx) ?? 1;
  ctx.shadowColor = color;
  ctx.shadowBlur = blur * k;
  if (offsetY !== undefined) ctx.shadowOffsetY = offsetY * k;
}

export function rr(ctx: Ctx, x: number, y: number, w: number, h: number, r: number | number[]): void {
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, w, h, r);
  else ctx.rect(x, y, w, h);
}

export function wrap(ctx: Ctx, s: string, maxW: number): string[] {
  const out: string[] = [];
  let line = '';
  for (const w of String(s).split(/\s+/)) {
    const next = line ? line + ' ' + w : w;
    if (line && ctx.measureText(next).width > maxW) {
      out.push(line);
      line = w;
    } else line = next;
  }
  if (line) out.push(line);
  return out;
}

export function setFont(ctx: Ctx, weight: number, size: number, font: string): void {
  ctx.textRendering = 'geometricPrecision';
  ctx.fontKerning = 'normal';
  ctx.font = weight + ' ' + size + 'px ' + font;
}

/** Paint in finer font units because some canvas backends still round glyph metrics under geometricPrecision. */
export function paintText(ctx: Ctx, s: string, x: number, y: number, stroke = false): void {
  const k = 16;
  ctx.save();
  ctx.scale(1 / k, 1 / k);
  ctx.font = ctx.font.replace(/([\d.]+)px/, (_, size: string) => Number(size) * k + 'px');
  if (stroke) {
    ctx.lineWidth *= k;
    ctx.strokeText(s, x * k, y * k);
  } else ctx.fillText(s, x * k, y * k);
  ctx.restore();
}

export function text(
  ctx: Ctx, s: string, x: number, y: number, size: number, color: string, font: string,
  weight = 600, align: CanvasTextAlign = 'left',
): void {
  setFont(ctx, weight, size, font);
  ctx.fillStyle = color;
  ctx.textAlign = align;
  paintText(ctx, s, x, y);
  ctx.textAlign = 'left';
}
