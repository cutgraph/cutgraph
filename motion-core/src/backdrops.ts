// The staging backdrops, beside scene.ts's five: `black`, an empty frame for title cards, and
// `vision`, a generic bright radial backdrop with rotating rays and outward pixel sparks.
// Pure functions of time.
import type { Ctx } from './types.ts';

const RAYS = 14;
const SPARKS = 28;
const GOLDEN = 2.399963;

/** Paints `id` when it is a staging backdrop, and says whether it was. */
export function stagedBackdrop(ctx: Ctx, id: string, W: number, H: number, t: number): boolean {
  if (id === 'black') {
    ctx.fillStyle = '#000000';
    ctx.fillRect(0, 0, W, H);
    return true;
  }
  if (id !== 'vision') return false;
  const cx = W / 2, cy = H / 2, R = Math.hypot(W, H) / 2, u = Math.min(W, H) / 1080;
  const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
  glow.addColorStop(0, '#fff6cf');
  glow.addColorStop(0.3, '#ffd23f');
  glow.addColorStop(0.72, '#ff9f1c');
  glow.addColorStop(1, '#f2542d');
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, W, H);
  ctx.save();
  ctx.fillStyle = 'rgba(255,255,255,.2)';
  const half = Math.PI / RAYS / 1.25;
  for (let k = 0; k < RAYS; k++) {
    const a = t * 0.5 + (k * 2 * Math.PI) / RAYS;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(a - half) * R * 1.2, cy + Math.sin(a - half) * R * 1.2);
    ctx.lineTo(cx + Math.cos(a + half) * R * 1.2, cy + Math.sin(a + half) * R * 1.2);
    ctx.closePath();
    ctx.fill();
  }
  for (let k = 0; k < SPARKS; k++) {
    const p = (t * (0.35 + (k % 5) * 0.08) + ((k * 0.137) % 1)) % 1, r = p * p * R, q = Math.max(2, Math.round((4 + 12 * p) * u));
    ctx.globalAlpha = Math.min(1, p * 4) * 0.9;
    ctx.fillStyle = k % 3 ? '#ffffff' : '#fff1a8';
    ctx.fillRect(Math.round(cx + Math.cos(k * GOLDEN) * r - q / 2), Math.round(cy + Math.sin(k * GOLDEN) * r - q / 2), q, q);
  }
  ctx.restore();
  return true;
}
