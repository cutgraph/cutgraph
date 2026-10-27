import type { SceneSpan } from "../model";

export interface ArcBlock {
  span: SceneSpan;
  /** Timeline frame the scene starts at. */
  startF: number;
  /** Fractions (0..1) of the arc's width before this block and taken by it, gaps excluded. */
  left: number;
  width: number;
}

export interface ArcLayout {
  blocks: ArcBlock[];
  totalF: number;
}

// Scenes still on the timeline, in story order, each as wide as its kept duration. A fully cut scene
// has no time to show, so it has no block.
export function arcLayout(spans: SceneSpan[]): ArcLayout {
  const shown = spans.filter((s) => s.tlStartF !== null && s.durF > 0);
  const totalF = shown.reduce((acc, s) => acc + s.durF, 0);
  let before = 0;
  const blocks = shown.map((span) => {
    const block = { span, startF: span.tlStartF ?? 0, left: before / totalF, width: span.durF / totalF };
    before += span.durF;
    return block;
  });
  return { blocks, totalF };
}

function nearestBefore(blocks: ArcBlock[], frame: number): number {
  let best = -1;
  blocks.forEach((b, i) => {
    if (b.startF <= frame && (best < 0 || b.startF > blocks[best].startF)) best = i;
  });
  if (best >= 0) return best;
  return blocks.reduce((first, b, i) => (b.startF < blocks[first].startF ? i : first), 0);
}

// The block playing `playheadF` and the arc fraction the marker sits at. In a gap or past the end the
// marker rests on the end of the scene that played last; before every scene, on the first one.
export function arcPlayhead(layout: ArcLayout, playheadF: number): { index: number; at: number } | null {
  const { blocks, totalF } = layout;
  if (!blocks.length) return null;
  const inside = blocks.findIndex((b) => playheadF >= b.startF && playheadF < b.startF + b.span.durF);
  const index = inside >= 0 ? inside : nearestBefore(blocks, playheadF);
  const block = blocks[index];
  const offset = Math.min(Math.max(playheadF - block.startF, 0), block.span.durF);
  return { index, at: block.left + offset / totalF };
}
