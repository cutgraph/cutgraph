// Pure waveform reduction for the canvas strip. Peaks arrive as raw s16le [min, max] pairs (one per
// 50 ms window); a strip only has ~1 px per column, so bucket them down to `width` columns — taking
// the min-of-mins / max-of-maxes per bucket — and normalize to [-1, 1] for drawing. Kept pure (no
// canvas / DOM) so it unit-tests under vitest.

const S16_MAX = 32768;

export function columns(peaks: [number, number][], width: number): [number, number][] {
  if (width <= 0 || peaks.length === 0) return [];
  const n = Math.min(width, peaks.length);
  const out: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    const lo = Math.floor((i * peaks.length) / n);
    const hi = Math.max(lo + 1, Math.floor(((i + 1) * peaks.length) / n));
    let mn = peaks[lo][0];
    let mx = peaks[lo][1];
    for (let j = lo + 1; j < hi; j++) {
      if (peaks[j][0] < mn) mn = peaks[j][0];
      if (peaks[j][1] > mx) mx = peaks[j][1];
    }
    out.push([mn / S16_MAX, mx / S16_MAX]);
  }
  return out;
}
