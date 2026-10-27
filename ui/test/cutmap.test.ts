import { describe, it, expect } from "vitest";
import type { Project, Clip } from "../src/types/project";
import {
  sec_to_frame,
  wordSrcFrame,
  srcToTimeline,
  timelineToSrc,
  speechClipAt,
  wordSpanToCut,
  srcRangeToTimeline,
  timedWords,
  currentWordIdx,
  resolveTimed,
} from "../src/transcript/cutmap";
import type { TranscriptWord } from "../src/api";

const MEDIA = "src.mp4";

// A source (0..300f) with frames 100..149 removed: clip A keeps [0,100), clip B keeps [150,300)
// shifted left by 50 so it abuts A on the timeline. Frames 100..149 are covered by NO clip = cut.
function cutProject(): Project {
  const clips: Clip[] = [
    { id: "A", media: { source: MEDIA, proxy: null }, srcInF: 0, srcOutF: 100, tlStartF: 0 },
    { id: "B", media: { source: MEDIA, proxy: null }, srcInF: 150, srcOutF: 300, tlStartF: 100 },
  ];
  return {
    fps: 30, width: 1920, height: 1080, name: "p", revision: 1, schemaVersion: 1,
    tracks: [{ id: "t1", kind: "speech", clips }],
  };
}

describe("sec_to_frame", () => {
  it("rounds half away from zero (not banker's)", () => {
    expect(sec_to_frame(0.05, 30)).toBe(2); // 1.5 -> 2
    expect(sec_to_frame(1 / 60, 30)).toBe(1); // 0.5 -> 1
    expect(sec_to_frame(0, 30)).toBe(0);
  });
  it("handles negative input symmetrically", () => {
    expect(sec_to_frame(-0.05, 30)).toBe(-2); // -1.5 -> -2
  });
  it("wordSrcFrame derives from the word start", () => {
    expect(wordSrcFrame({ w: "hi", start: 0.05, end: 0.2, segIdx: 0 }, 30)).toBe(2);
  });
});

describe("srcToTimeline", () => {
  it("maps a covered source frame into the shifted timeline", () => {
    const p = cutProject();
    expect(srcToTimeline(p, MEDIA, 50)).toBe(50); // clip A, no shift
    expect(srcToTimeline(p, MEDIA, 99)).toBe(99); // last frame of A (srcOutF exclusive)
    expect(srcToTimeline(p, MEDIA, 200)).toBe(150); // clip B: 100 + (200-150)
  });
  it("returns null for a frame inside the removed region (word is 'cut')", () => {
    const p = cutProject();
    expect(srcToTimeline(p, MEDIA, 100)).toBeNull();
    expect(srcToTimeline(p, MEDIA, 149)).toBeNull();
  });
  it("returns null for a different media (speech-track-only)", () => {
    expect(srcToTimeline(cutProject(), "other.mp4", 50)).toBeNull();
  });
});

describe("timelineToSrc", () => {
  it("round-trips with srcToTimeline for surviving frames", () => {
    const p = cutProject();
    for (const srcF of [0, 50, 99, 150, 200, 299]) {
      const tl = srcToTimeline(p, MEDIA, srcF);
      expect(tl).not.toBeNull();
      expect(timelineToSrc(p, tl as number)).toEqual({ media: MEDIA, srcF });
    }
  });
  it("returns null past the end of the timeline", () => {
    expect(timelineToSrc(cutProject(), 250)).toBeNull(); // A(0..99)+B(100..249) end
  });
});

describe("speechClipAt", () => {
  it("finds the covering clip or null in a cut region", () => {
    const p = cutProject();
    expect(speechClipAt(p, MEDIA, 50)?.id).toBe("A");
    expect(speechClipAt(p, MEDIA, 200)?.id).toBe("B");
    expect(speechClipAt(p, MEDIA, 120)).toBeNull();
  });
});

// A single full clip [0,300) at fps 30: source frame == timeline frame (no shift).
function fullProject(): Project {
  const clips: Clip[] = [
    { id: "A", media: { source: MEDIA, proxy: null }, srcInF: 0, srcOutF: 300, tlStartF: 0 },
  ];
  return {
    fps: 30, width: 1920, height: 1080, name: "p", revision: 1, schemaVersion: 1,
    tracks: [{ id: "t1", kind: "speech", clips }],
  };
}

const word = (start: number, end: number): TranscriptWord => ({ w: "x", start, end, segIdx: 0 });

// wordSpanToCut now returns SOURCE ranges, clipped to the words selected and split into one
// run per KEPT region — a span crossing a struck gap yields multiple runs, never an over-covering one.
describe("wordSpanToCut (source space)", () => {
  it("maps first.start..last.end to a single source run inside one clip", () => {
    const p = fullProject();
    // 1.0s→30 .. 2.4s→72.
    expect(wordSpanToCut(p, MEDIA, word(1.0, 1.4), word(2.0, 2.4), 30)).toEqual([
      { srcStartF: 30, srcEndF: 72 },
    ]);
  });
  it("covers a single word (first === last)", () => {
    const p = fullProject();
    expect(wordSpanToCut(p, MEDIA, word(1.0, 1.4), word(1.0, 1.4), 30)).toEqual([
      { srcStartF: 30, srcEndF: 42 },
    ]);
  });
  it("a selection across a struck gap yields TWO source runs, not one over-covering range", () => {
    // src 50 → src 200 straddles the removed [100,150): kept runs are [50,100) and [150,200).
    expect(wordSpanToCut(cutProject(), MEDIA, word(50 / 30, 60 / 30), word(190 / 30, 200 / 30), 30))
      .toEqual([
        { srcStartF: 50, srcEndF: 100 },
        { srcStartF: 150, srcEndF: 200 },
      ]);
  });
  it("a selection ending on the last word before the gap is the exact single range", () => {
    // src 20 → src 100 (last kept frame before the gap): exactly [20,100), no phantom over-cut.
    expect(wordSpanToCut(cutProject(), MEDIA, word(20 / 30, 30 / 30), word(90 / 30, 100 / 30), 30))
      .toEqual([{ srcStartF: 20, srcEndF: 100 }]);
  });
  it("returns [] when the whole span is inside a cut region", () => {
    expect(wordSpanToCut(cutProject(), MEDIA, word(110 / 30, 120 / 30), word(130 / 30, 140 / 30), 30))
      .toEqual([]);
  });
  it("returns [] for a zero/negative-width span", () => {
    expect(wordSpanToCut(fullProject(), MEDIA, word(2.0, 2.0), word(1.0, 1.0), 30)).toEqual([]);
  });
});

// Accept-time conversion: a stored source range maps to timeline range(s) through the CURRENT
// clip list — split at gaps, dropped when fully cut. Never uses a raw source delta as a timeline delta.
describe("srcRangeToTimeline", () => {
  it("splits a range that spans a struck gap into two adjacent timeline ranges", () => {
    // src [50,200) → clip A [50,100)→tl[50,100); clip B [150,200)→tl[100,150).
    expect(srcRangeToTimeline(cutProject(), MEDIA, 50, 200)).toEqual([
      { startF: 50, endF: 100 },
      { startF: 100, endF: 150 },
    ]);
  });
  it("maps a range fully inside one clip to a single shifted timeline range", () => {
    // src [160,200) in clip B (srcInF 150, tlStartF 100) → tl [110,150).
    expect(srcRangeToTimeline(cutProject(), MEDIA, 160, 200)).toEqual([{ startF: 110, endF: 150 }]);
  });
  it("drops a range that is now fully cut (empty result)", () => {
    expect(srcRangeToTimeline(cutProject(), MEDIA, 110, 140)).toEqual([]);
  });
  it("drops a range for a different media", () => {
    expect(srcRangeToTimeline(cutProject(), "other.mp4", 50, 90)).toEqual([]);
  });
});

// WhisperX leaves some tokens unaligned (null start/end). Untimed words must never coerce to
// frame 0 and corrupt the karaoke binary search or the click/seek target.
const untimed = (w: string): TranscriptWord =>
  ({ w, start: null as unknown as number, end: null as unknown as number, segIdx: 0 });
const timedW = (w: string, start: number, end: number): TranscriptWord => ({ w, start, end, segIdx: 0 });

describe("timedWords / currentWordIdx", () => {
  const words = [
    timedW("a", 1, 1.5),
    timedW("b", 2, 2.5),
    untimed("num"), // unaligned token mid-paragraph
    timedW("c", 3, 3.5),
  ];
  const timed = timedWords(words);

  it("timedWords keeps only aligned words, carrying their flat index", () => {
    expect(timed).toEqual([
      { idx: 0, start: 1, end: 1.5 },
      { idx: 1, start: 2, end: 2.5 },
      { idx: 3, start: 3, end: 3.5 },
    ]);
  });
  it("resolves the current word past a null-start word without search corruption", () => {
    expect(currentWordIdx(timed, 2.2, new Set())).toBe(1);
    // The old null->0 coercion broke monotonicity here; the timed subsequence keeps it correct.
    expect(currentWordIdx(timed, 3.2, new Set())).toBe(3);
  });
  it("returns -1 in an inter-word gap and for a struck word", () => {
    expect(currentWordIdx(timed, 1.7, new Set())).toBe(-1); // after 'a'.end, before 'b'.start
    expect(currentWordIdx(timed, 2.2, new Set([1]))).toBe(-1); // 'b' is struck
  });
  it("never returns the flat index of an untimed word", () => {
    for (let s = 0; s <= 4; s += 0.1) {
      expect(currentWordIdx(timed, s, new Set())).not.toBe(2);
    }
  });
});

describe("resolveTimed", () => {
  it("resolves untimed words to the nearest timed neighbor (previous preferred, else next)", () => {
    const words = [untimed("lead"), timedW("a", 1, 1.5), untimed("mid"), timedW("b", 3, 3.5), untimed("tail")];
    // lead has no previous timed → next (1); mid → previous (1); tail → previous (3).
    expect(resolveTimed(words)).toEqual([1, 1, 1, 3, 3]);
  });
  it("returns -1 for every word when none are timed", () => {
    expect(resolveTimed([untimed("x"), untimed("y")])).toEqual([-1, -1]);
  });
  it("is the identity on a fully-timed list", () => {
    expect(resolveTimed([timedW("a", 1, 1.5), timedW("b", 2, 2.5)])).toEqual([0, 1]);
  });
});
