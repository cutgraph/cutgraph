import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { type Canvas, GlobalFonts, createCanvas } from '@napi-rs/canvas';
import type { Ctx, MotionEnv, RigCanvas } from '../src/index.ts';

const FONT_DIR = fileURLToPath(new URL('../../assets/fonts/inter/', import.meta.url));
const FONTS = ['Inter-Regular.ttf', 'Inter-SemiBold.ttf', 'Inter-Bold.ttf', 'Inter-Black.ttf'];

let registered = false;

/** Explicit font registration shared by rendering and pixel tests. */
export function registerFonts(required = false): boolean {
  const missing = FONTS.filter((f) => !existsSync(FONT_DIR + '/' + f));
  if (required && missing.length) throw new Error(`Required test fonts missing from ${FONT_DIR}: ${missing.join(', ')}`);
  if (!registered && !missing.length) {
    GlobalFonts.removeAll();
    for (const f of FONTS) {
      const path = FONT_DIR + '/' + f;
      if (!GlobalFonts.registerFromPath(path, 'Inter')) throw new Error(`Could not register font: ${path}`);
    }
    registered = true;
  }
  return missing.length === 0;
}

/** Fingerprint exactly the files registered above, rather than the system font inventory. */
export function fontPrint(): string {
  const h = createHash('sha256');
  for (const f of FONTS) {
    const path = FONT_DIR + '/' + f;
    h.update(f + ':' + (existsSync(path) ? createHash('sha256').update(readFileSync(path)).digest('hex') : 'missing') + '\n');
  }
  return h.digest('hex').slice(0, 16);
}

/**
 * A Node env backed by @napi-rs/canvas, with Inter registered so text renders the same every run.
 * `images` are the decoded images a shot names (`screen.image`), loaded before the first frame.
 */
export function nodeEnv(images?: ReadonlyMap<string, CanvasImageSource>): MotionEnv {
  registerFonts(true);
  return {
    createCanvas: (w, h) => createCanvas(w, h) as unknown as RigCanvas,
    fontFamily: '"Inter"',
    image: (src) => images?.get(src) ?? null,
  };
}

// @napi-rs/canvas implements the Canvas 2D API, but its context type is not the DOM's nominally.
export function surface(w: number, h: number): { canvas: Canvas; ctx: Ctx } {
  const canvas = createCanvas(w, h);
  return { canvas, ctx: canvas.getContext('2d') as unknown as Ctx };
}
