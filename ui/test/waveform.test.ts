import { describe, it, expect } from "vitest";
import { columns } from "../src/transcript/waveform";

describe("columns", () => {
  it("returns empty for no peaks or non-positive width", () => {
    expect(columns([], 100)).toEqual([]);
    expect(columns([[-1, 1]], 0)).toEqual([]);
  });

  it("passes peaks through (normalized) when width >= count", () => {
    const cols = columns([[-16384, 16384]], 100);
    expect(cols).toEqual([[-0.5, 0.5]]);
  });

  it("buckets min-of-mins / max-of-maxes when downsampling", () => {
    // 4 windows → 2 columns: bucket [0,2) and [2,4).
    const peaks: [number, number][] = [
      [-8000, 4000],
      [-2000, 16000],
      [-16000, 2000],
      [-1000, 8000],
    ];
    const cols = columns(peaks, 2);
    expect(cols.length).toBe(2);
    // column 0 spans the first two windows: min -8000, max 16000.
    expect(cols[0]).toEqual([-8000 / 32768, 16000 / 32768]);
    // column 1 spans the last two: min -16000, max 8000.
    expect(cols[1]).toEqual([-16000 / 32768, 8000 / 32768]);
  });

  it("reports the true envelope for all-positive and all-negative buckets", () => {
    // Buckets that never straddle zero must not floor min at 0 / ceil max at 0.
    const peaks: [number, number][] = [
      [2000, 4000],
      [1000, 8000], // bucket [0,2): all positive → min 1000, max 8000
      [-8000, -2000],
      [-4000, -1000], // bucket [2,4): all negative → min -8000, max -1000
    ];
    const cols = columns(peaks, 2);
    expect(cols[0]).toEqual([1000 / 32768, 8000 / 32768]);
    expect(cols[1]).toEqual([-8000 / 32768, -1000 / 32768]);
  });
});
