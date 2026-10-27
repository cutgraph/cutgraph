// Rainy Day Museum Visit: seven independently timed travel tips.
import { createHash } from 'node:crypto';
import { FORMATS, drawSceneFrame, resolveShot, shotTiming } from '../src/index.ts';
import type { Format, MotionEnv, ProjectShot } from '../src/index.ts';
import { registerFonts, surface } from '../scripts/node-env.ts';

function mapImage(): CanvasImageSource {
  registerFonts(true);
  const { canvas, ctx } = surface(320, 480);
  ctx.fillStyle = '#e7eef0';
  ctx.fillRect(0, 0, 320, 480);
  ctx.strokeStyle = '#475569';
  ctx.lineWidth = 8;
  ctx.strokeRect(28, 100, 264, 320);
  ctx.beginPath();
  ctx.moveTo(160, 420);
  ctx.lineTo(160, 260);
  ctx.lineTo(64, 260);
  ctx.stroke();
  ctx.fillStyle = '#2bb3a3';
  ctx.fillRect(48, 160, 72, 72);
  ctx.fillRect(200, 304, 64, 64);
  ctx.fillStyle = '#334155';
  ctx.font = 'bold 28px "Inter"';
  ctx.fillText('Museum map', 28, 56);
  return canvas as unknown as CanvasImageSource;
}

export const FILM_IMAGES = new Map([['museum-map', mapImage()]]);

export interface FilmShot {
  name: string;
  role: string;
  shot: ProjectShot;
  /** Scene length and the cue word, in seconds from the scene start. */
  dur: number;
  cue: number | null;
}

export const FILM_SHOTS: FilmShot[] = [
  { name: 'title', role: 'Beat', dur: 2.7, cue: null,
    shot: { mode: 'animation', character: 'lens', layout: 'title', backdrop: 'black', headline: 'Rainy Day Museum Visit', eyebrow: '', badges: [{ text: 'INDOORS', tone: 'accent' }], accent: '#2bb3a3' } },
  { name: 'meet', role: 'Beat', dur: 4.1, cue: 1.1,
    shot: { mode: 'animation', character: 'lens', backdrop: 'work', diagram: 'none', headline: 'Meet at the entrance', pose: 'wave', move: 'wave', expression: 'happy', costar: { character: 'lens', pose: 'rest', move: 'idle', bubble: 'say' }, ensemble: [{ character: 'lens', pose: 'point', move: 'nod' }], headlineAt: 'costar' } },
  { name: 'route', role: 'Beat', dur: 3.7, cue: 0.6,
    shot: { mode: 'animation', character: 'lens', backdrop: 'route', diagram: 'grid', hideHeadline: true, pose: 'point', move: 'idle', boardLabels: ['Map', 'Gallery', 'Cafe'] } },
  { name: 'tickets', role: 'Beat', dur: 2.3, cue: null,
    shot: { mode: 'animation', character: 'lens', layout: 'screen', backdrop: 'work', headline: 'Keep your ticket handy', eyebrow: '', screen: { image: 'museum-map' }, accent: '#2bb3a3' } },
  { name: 'gallery', role: 'Beat', dur: 5.3, cue: null,
    shot: { mode: 'animation', character: 'lens', camera: 'pan', backdrop: 'work', diagram: 'none', headline: 'Choose one gallery', pose: 'rest', expression: 'curious', move: 'idle', costar: { character: 'lens', pose: 'point', move: 'idle' } } },
  { name: 'ask', role: 'Beat', dur: 3.9, cue: 1.7,
    shot: { mode: 'animation', character: 'lens', backdrop: 'result', diagram: 'none', headline: 'Ask about quiet rooms', pose: 'rest', move: 'nod', expression: 'curious', bubble: 'question' } },
  { name: 'signoff', role: 'Wrap', dur: 2.9, cue: null,
    shot: { mode: 'animation', character: 'lens', layout: 'signoff', backdrop: 'black', headline: 'Enjoy your visit', eyebrow: '', mark: 'TOUR', pose: 'rest', expression: 'happy', move: 'idle', accent: '#2bb3a3' } },
];

const FPS = 30;

/** sha256 (16 hex) of one shot's frames across its scene, in one format, drawn at a quarter size. */
export function filmShotHash(f: FilmShot, format: Format, env: MotionEnv): string {
  const filmEnv: MotionEnv = { ...env, image: (src) => FILM_IMAGES.get(src) ?? env.image?.(src) ?? null };
  const [W, H] = FORMATS[format], h = createHash('sha256');
  const timing = shotTiming(0, 12, f.cue === null ? null : Math.round(f.cue * FPS), FPS, Math.round(f.dur * FPS));
  const shot = resolveShot(f.shot, f.role, f.name);
  for (const lt of [0, 0.35, 0.9, 1.6, 2.4, f.dur - 0.5, f.dur - 1 / FPS]) {
    if (lt < 0 || lt >= f.dur) continue;
    const { ctx } = surface(W / 4, H / 4);
    ctx.scale(0.25, 0.25);
    drawSceneFrame(ctx, f.shot.mode ?? 'animation', shot, lt, timing, W, H, filmEnv);
    h.update(ctx.getImageData(0, 0, W / 4, H / 4).data);
  }
  return h.digest('hex').slice(0, 16);
}
