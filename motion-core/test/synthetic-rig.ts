import type { Character } from '../src/index.ts';

export const SYNTHETIC_RIG: Character = {
  id: 'sample-rig', name: 'Sample', owner: 'client',
  palettes: ['fog', 'fern', 'clay'], rows: 50, aspect: 0.6, crown: 47, scale: 1,
  parts: { brows: ['level', 'raised'], eyes: ['open', 'closed'], mouth: ['closed', 'open'] },
  expressions: {
    neutral: { brows: 'level', eyes: 'open', mouth: 'closed' },
    happy: { brows: 'raised', eyes: 'open', mouth: 'open' },
    curious: { brows: 'raised', eyes: 'open', mouth: 'closed' },
  },
  poses: ['rest', 'wave', 'point'], moves: ['idle', 'nod', 'wave'],
  draw(ctx, cx, by, h, state) {
    const q = Math.max(1, Math.floor(h / 50));
    const face = state.custom ?? this.expressions[state.expression ?? 'neutral'] ?? this.expressions.neutral;
    ctx.save();
    ctx.translate(Math.round(cx), Math.round(by));
    ctx.scale(q, q);
    const colors: Record<string, string> = { fog: '#64748b', fern: '#4d7c60', clay: '#ba7355' };
    ctx.fillStyle = colors[state.palette ?? 'fog'] ?? colors.fog;
    ctx.fillRect(-10, -25, 20, 20);
    ctx.fillRect(-12, -47, 24, 20);
    ctx.fillRect(-9, -5, 6, 5);
    ctx.fillRect(3, -5, 6, 5);
    ctx.fillRect(10, state.pose === 'wave' ? -40 : -24, state.pose === 'point' ? 12 : 5, 5);
    ctx.fillStyle = '#eef2f4';
    ctx.fillRect(-8, -40, 4, face.eyes === 'closed' ? 1 : 4);
    ctx.fillRect(4, -40, 4, face.eyes === 'closed' ? 1 : 4);
    ctx.fillRect(-4, -33, 8, face.mouth === 'open' ? 4 : 1);
    ctx.fillStyle = '#1e293b';
    ctx.fillRect(-8, face.brows === 'raised' ? -45 : -43, 4, 1);
    ctx.fillRect(4, face.brows === 'raised' ? -45 : -43, 4, 1);
    ctx.restore();
  },
};
