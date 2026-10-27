import { drawCharacter } from './characters.ts';
import { clamp, ease, paintText, fontOf, prog, rr, setFont, text, wrap } from './draw.ts';
import { ACCENT, EYEBROW, layoutFor } from './scene.ts';
import type { Ctx, Cue, FontEnv, GraphicId, MotionEnv, Word } from './types.ts';

export const GRAPHICS: readonly GraphicId[] = ['lower-third', 'title', 'callout', 'steps', 'character-cameo'];

/** A graphics overlay at visibility `k` (0 hidden .. 1 fully in); `t` is timeline time. */
export function drawGraphic(ctx: Ctx, c: Cue, k: number, W: number, H: number, t: number, env: MotionEnv): void {
  if (k <= 0) return;
  const land = W > H, u = Math.min(W, H) / 1080, font = fontOf(env);
  ctx.save();
  ctx.textBaseline = 'alphabetic';
  ctx.textAlign = 'left';
  ctx.globalAlpha = clamp(k * 1.4);
  if (c.item === 'lower-third') {
    const [name, role] = String(c.text || 'Your name · Studio').split('·').map((s) => s.trim());
    const w = Math.min(W * 0.62, 900 * u), h = 150 * u, x = W * 0.05 - (1 - k) * (w + W * 0.05), y = land ? H * 0.68 : H * 0.62;
    ctx.fillStyle = 'rgba(12,15,18,.9)';
    rr(ctx, x, y, w, h, 14 * u);
    ctx.fill();
    ctx.fillStyle = ACCENT;
    ctx.fillRect(x, y, 12 * u, h);
    text(ctx, name || '', x + 44 * u, y + 68 * u, Math.round(50 * u), '#eef2f4', font, 700);
    text(ctx, role || '', x + 44 * u, y + 118 * u, Math.round(30 * u), '#9fb0bb', font, 500);
  } else if (c.item === 'title') {
    const s = 0.9 + 0.1 * ease.back(k);
    ctx.translate(W / 2, H * 0.45);
    ctx.scale(s, s);
    ctx.fillStyle = 'rgba(12,15,18,.88)';
    rr(ctx, -W * 0.36, -150 * u, W * 0.72, 300 * u, 24 * u);
    ctx.fill();
    text(ctx, EYEBROW, 0, -50 * u, Math.round(28 * u), ACCENT, font, 800, 'center');
    setFont(ctx, 700, Math.round(72 * u), font);
    ctx.fillStyle = '#eef2f4';
    ctx.textAlign = 'center';
    wrap(ctx, c.text || 'Title', W * 0.64).slice(0, 2).forEach((l, i) => paintText(ctx, l, 0, 50 * u + i * 80 * u));
  } else if (c.item === 'callout') {
    const p = ease.back(k), x = land ? W * 0.7 : W * 0.5, y = land ? H * 0.2 : H * 0.26;
    ctx.translate(x, y);
    ctx.scale(p, p);
    setFont(ctx, 700, Math.round(44 * u), font);
    const w = ctx.measureText(c.text || 'Callout').width + 80 * u;
    rr(ctx, -w / 2, -52 * u, w, 104 * u, 52 * u);
    ctx.fillStyle = ACCENT;
    ctx.fill();
    ctx.fillStyle = '#111214';
    ctx.textAlign = 'center';
    paintText(ctx, c.text || 'Callout', 0, 15 * u);
  } else if (c.item === 'steps') {
    const labels = String(c.text || 'One · Two · Three').split('·').map((s) => s.trim()).slice(0, 3);
    const bw = land ? W * 0.24 : W * 0.8, bh = 130 * u, gap = land ? W * 0.03 : 24 * u;
    const total = land ? bw * 3 + gap * 2 : bh * 3 + gap * 2, x0 = land ? (W - total) / 2 : W * 0.1, y0 = land ? H * 0.64 : H * 0.42;
    labels.forEach((l, i) => {
      const kk = ease.out(prog(t, (c.start || 0) + i * 0.45, 0.4));
      if (kk <= 0) return;
      const x = land ? x0 + i * (bw + gap) : x0, y = land ? y0 + (1 - kk) * 30 : y0 + i * (bh + gap) + (1 - kk) * 30;
      ctx.save();
      ctx.globalAlpha = clamp(k * 1.4) * kk;
      ctx.fillStyle = 'rgba(12,15,18,.9)';
      rr(ctx, x, y, bw, bh, 16 * u);
      ctx.fill();
      ctx.fillStyle = ACCENT;
      ctx.beginPath();
      ctx.arc(x + 58 * u, y + bh / 2, 30 * u, 0, Math.PI * 2);
      ctx.fill();
      text(ctx, String(i + 1), x + 58 * u, y + bh / 2 + 12 * u, Math.round(34 * u), '#111214', font, 800, 'center');
      setFont(ctx, 600, Math.round(32 * u), font);
      ctx.fillStyle = '#eef2f4';
      wrap(ctx, l, bw - 130 * u).slice(0, 2).forEach((ln, j, arr) => {
        paintText(ctx, ln, x + 108 * u, y + bh / 2 + 11 * u - (arr.length - 1) * 19 * u + j * 38 * u);
      });
      ctx.restore();
    });
  } else if (c.item === 'character-cameo') {
    const h = land ? H * 0.34 : H * 0.2, cx = land ? W * 0.86 : W * 0.78;
    const by = (land ? H * 0.95 : H * 0.74) + (1 - ease.back(k)) * h * 1.2;
    ctx.globalAlpha = 1;
    drawCharacter(ctx, cx, by, h, { pose: 'wave', expression: 'happy', move: 'wave', t: t - (c.start || 0) }, env);
  }
  ctx.restore();
}

/**
 * Karaoke captions for one scene's words: the line holding time `t` is shown, the spoken word is
 * highlighted, spoken words are solid and upcoming words are dimmed. Cut words are skipped.
 */
export function drawCaptions(ctx: Ctx, words: readonly Word[], t: number, W: number, H: number, env?: FontEnv): void {
  const kept = words.filter((w) => !w.cut);
  if (!kept.length) return;
  const L = layoutFor(W, H), fs = L.capFs;
  ctx.save();
  ctx.textBaseline = 'alphabetic';
  ctx.textAlign = 'left';
  setFont(ctx, 700, fs, fontOf(env));
  const sp = ctx.measureText(' ').width, maxW = W * 0.82, lines: Word[][] = [];
  let cur: Word[] = [], w = 0;
  for (const x of kept) {
    const ww = ctx.measureText(x.w).width;
    if (cur.length && w + sp + ww > maxW) { lines.push(cur); cur = []; w = 0; }
    w += (cur.length ? sp : 0) + ww;
    cur.push(x);
  }
  lines.push(cur);
  let li = lines.findIndex((l) => t < l[l.length - 1].end + 0.05);
  if (li < 0) li = lines.length - 1;
  const line = lines[li], widths = line.map((x) => ctx.measureText(x.w).width);
  const total = widths.reduce((a, b) => a + b, 0) + sp * (line.length - 1);
  let x = (W - total) / 2;
  const y = L.capY;
  rr(ctx, x - fs * 0.6, y - fs * 1.1, total + fs * 1.2, fs * 1.6, fs * 0.35);
  ctx.fillStyle = 'rgba(8,10,12,.72)';
  ctx.fill();
  line.forEach((r, k) => {
    ctx.fillStyle = t >= r.start && t < r.end ? ACCENT : t >= r.end ? '#f4f6f7' : 'rgba(244,246,247,.5)';
    paintText(ctx, r.w, x, y);
    x += widths[k] + sp;
  });
  ctx.restore();
}
