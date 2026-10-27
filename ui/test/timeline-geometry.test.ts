import { describe, it, expect } from "vitest";
import type { Project } from "../src/types/project";
import {
  clipPeakWindow,
  fitPxPerFrame,
  frameToPx,
  mmss,
  projectDurationF,
  pxToFrame,
  rulerTicks,
} from "../src/timeline/geometry";

describe("mmss", () => {
  it("formats floored whole seconds as M:SS", () => {
    expect(mmss(0, 30)).toBe("0:00");
    expect(mmss(90, 30)).toBe("0:03");
    expect(mmss(1830, 30)).toBe("1:01");
  });
});

describe("projectDurationF", () => {
  it("is the max end frame over clips and items across tracks", () => {
    const project: Project = {
      fps: 30,
      width: 1920,
      height: 1080,
      name: "p",
      revision: 1,
      schemaVersion: 1,
      tracks: [
        {
          id: "speech",
          kind: "speech",
          clips: [
            { id: "a", media: { source: "s.mp4", proxy: null }, srcInF: 0, srcOutF: 100, tlStartF: 0 },
            // ends at tlStartF(100) + dur(150) = 250
            { id: "b", media: { source: "s.mp4", proxy: null }, srcInF: 200, srcOutF: 350, tlStartF: 100 },
          ],
        },
        {
          id: "broll",
          kind: "video",
          // ends at 50 + 40 = 90
          clips: [{ id: "c", media: { source: "b.mp4", proxy: null }, srcInF: 0, srcOutF: 40, tlStartF: 50 }],
        },
        {
          id: "txt",
          kind: "text",
          // ends at 120 + 60 = 180
          items: [{ id: "t", text: "hi", preset: "hook", pos: { x: 0.5, y: 0.2 }, tlStartF: 120, durF: 60 }],
        },
      ],
    };
    expect(projectDurationF(project)).toBe(250);
  });

  it("is 0 for an empty project", () => {
    const project: Project = {
      fps: 30, width: 1920, height: 1080, name: "p", revision: 1, schemaVersion: 1, tracks: [],
    };
    expect(projectDurationF(project)).toBe(0);
  });
});

describe("frameToPx / pxToFrame", () => {
  it("round-trips and rounds half up at a .5 boundary", () => {
    // frame 3 at 0.5 px/frame → 1.5 px → back to 3 frames.
    expect(frameToPx(3, 0.5)).toBe(1.5);
    expect(pxToFrame(1.5, 0.5)).toBe(3);
    // 1.25 px / 0.5 = 2.5 → half-up → 3 (matches Math.round, not banker's).
    expect(pxToFrame(1.25, 0.5)).toBe(3);
  });
});

describe("fitPxPerFrame", () => {
  it("uses the visible space after the track gutter", () => {
    // 1120px viewport - 120px gutter leaves 1000px for 500 frames.
    expect(fitPxPerFrame(1120, 500, 120, 0.5)).toBe(2);
  });

  it("falls back before measurement and does not clamp an exact fit", () => {
    expect(fitPxPerFrame(0, 500, 120, 0.5)).toBe(0.5);
    // A long timeline needs less than the manual minimum zoom; Fit must still reach the right edge.
    expect(fitPxPerFrame(200, 100000, 120, 0.5)).toBeCloseTo(0.0008);
    // A short timeline needs more than the manual maximum zoom; Fit must still reach the right edge.
    expect(fitPxPerFrame(5000, 10, 120, 0.5)).toBe(488);
  });
});

describe("clipPeakWindow", () => {
  it("slices [startIdx, endIdx) via peakIdx = floor(srcF/fps*1000 / windowMs)", () => {
    // fps=30, windowMs=50: srcInF=45 → 1500ms → idx 30; srcOutF=90 → 3000ms → idx 60.
    const peaks: [number, number][] = Array.from({ length: 100 }, (_v, i) => [-i, i]);
    const win = clipPeakWindow(peaks, 45, 90, 30, 8000, 50);
    expect(win.length).toBe(30);
    expect(win[0]).toEqual([-30, 30]);
    expect(win[win.length - 1]).toEqual([-59, 59]);
  });
});

describe("rulerTicks", () => {
  it("puts ticks on whole-second boundaries at least 64px apart", () => {
    const fps = 30;
    const pxPerFrame = 1; // 30px/sec → step must be ceil(64/30)=3 seconds → 90 frames
    const ticks = rulerTicks(300, fps, pxPerFrame);
    expect(ticks.map((t) => t.frame)).toEqual([0, 90, 180, 270]);
    expect(ticks[0].label).toBe("0:00");
    expect(ticks[1].label).toBe("0:03");
    // whole-second boundaries + ≥64px spacing.
    for (const t of ticks) expect(t.frame % fps).toBe(0);
    for (let i = 1; i < ticks.length; i++) {
      expect((ticks[i].frame - ticks[i - 1].frame) * pxPerFrame).toBeGreaterThanOrEqual(64);
    }
  });

  it("is empty for zero duration", () => {
    expect(rulerTicks(0, 30, 1)).toEqual([]);
  });
});
