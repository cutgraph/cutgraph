import { describe, it, expect } from "vitest";
import type { Project, Clip } from "../src/types/project";
import {
  fillerToCut,
  fillerNeighborKept,
  interleaveFillers,
  batchCutLabel,
  timedWords,
  type KeptRange,
  type TimedWord,
} from "../src/transcript/cutmap";
import type { TranscriptWord, TranscriptFiller } from "../src/api";
import type { PendingCut } from "../src/store/useProjectStore";

const MEDIA = "src.mp4";

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

// Frames 100..149 removed: clip A keeps [0,100), clip B keeps [150,300).
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

// A filler token spanning source frames [a,b): start a/30 s, end b/30 s. Rides the SEPARATE fillers[]
// array — no `kind`, no word index; the cut derives purely from its times.
const filler = (a: number, b: number): TranscriptFiller =>
  ({ w: "um", start: a / 30, end: b / 30, segIdx: 0 });

describe("fillerToCut", () => {
  it("expands the token span by padF each side and intersects the speech clips", () => {
    const cut = fillerToCut(filler(50, 60), 2, 30, fullProject(), MEDIA, []);
    expect(cut).toEqual({ media: MEDIA, srcStartF: 48, srcEndF: 62, kind: "filler", durSec: 14 / 30, conf: 1 });
  });

  it("clips the left pad so it never crosses a kept intentional-pause range", () => {
    // A kept pause ends exactly at the word start (50): the −2 left pad must NOT enter it.
    const kept: KeptRange[] = [{ media: MEDIA, srcStartF: 0, srcEndF: 50 }];
    const cut = fillerToCut(filler(50, 60), 2, 30, fullProject(), MEDIA, kept);
    expect(cut?.srcStartF).toBe(50); // clipped up from 48
    expect(cut?.srcEndF).toBe(62);
  });

  it("clips the right pad so it never crosses a neighbouring kept word", () => {
    // A kept word begins exactly at the word end (60): the +2 right pad must NOT enter it.
    const kept: KeptRange[] = [{ media: MEDIA, srcStartF: 60, srcEndF: 90 }];
    const cut = fillerToCut(filler(50, 60), 2, 30, fullProject(), MEDIA, kept);
    expect(cut?.srcStartF).toBe(48);
    expect(cut?.srcEndF).toBe(60); // clipped down from 62
  });

  it("returns null when the filler sits entirely inside an already-cut region", () => {
    // Word [110,120) is inside the removed [100,150): no covering speech clip survives.
    expect(fillerToCut(filler(110, 120), 2, 30, cutProject(), MEDIA, [])).toBeNull();
  });

  it("returns null when a kept range straddles the filler word itself", () => {
    const kept: KeptRange[] = [{ media: MEDIA, srcStartF: 40, srcEndF: 70 }];
    expect(fillerToCut(filler(50, 60), 2, 30, fullProject(), MEDIA, kept)).toBeNull();
  });

  it("ignores kept ranges on another media", () => {
    const kept: KeptRange[] = [{ media: "other.mp4", srcStartF: 0, srcEndF: 55 }];
    const cut = fillerToCut(filler(50, 60), 2, 30, fullProject(), MEDIA, kept);
    expect(cut?.srcStartF).toBe(48); // the foreign kept range does not clip
  });

  it("a filler whose WORD is inside a cut region returns null, never a spurious padding sliver", () => {
    // Word [100,105) is the start of the removed [100,150). With padF=1 the LEFT pad (a=99) still
    // overlaps kept clip A [0,100), so the old `?? runs[0]` fallback emitted [99,100) — a frame of
    // UNRELATED kept content. The word itself has no covering run ⇒ the fix returns null.
    expect(fillerToCut(filler(100, 105), 1, 30, cutProject(), MEDIA, [])).toBeNull();
    // Word [148,150) ends at clip B's srcInF (150): padF=3 reaches B [150,153) but the word is uncovered.
    expect(fillerToCut(filler(148, 150), 3, 30, cutProject(), MEDIA, [])).toBeNull();
  });
});

describe("fillerNeighborKept — real neighbour-word protection", () => {
  it("bounds the pad by the prev word's end and the next word's start", () => {
    // prev word [40,46), next word [64,70); filler [50,60). padF=8 would reach [42,68].
    const timed: TimedWord[] = [
      { idx: 0, start: 40 / 30, end: 46 / 30 },
      { idx: 1, start: 64 / 30, end: 70 / 30 },
    ];
    const kept = fillerNeighborKept(filler(50, 60), timed, MEDIA, 30);
    const cut = fillerToCut(filler(50, 60), 8, 30, fullProject(), MEDIA, kept);
    expect(cut?.srcStartF).toBe(46); // clipped up from 42 to the prev word's end
    expect(cut?.srcEndF).toBe(64); // clipped down from 68 to the next word's start
  });

  it("a filler abutting the next word clips the right pad to that word's start (no eaten consonant)", () => {
    // prev word [40,50), next word [60,70); filler [50,60) abuts both with no gap. Any pad must vanish.
    const timed: TimedWord[] = [
      { idx: 0, start: 40 / 30, end: 50 / 30 },
      { idx: 1, start: 60 / 30, end: 70 / 30 },
    ];
    const kept = fillerNeighborKept(filler(50, 60), timed, MEDIA, 30);
    const cut = fillerToCut(filler(50, 60), 2, 30, fullProject(), MEDIA, kept);
    expect(cut?.srcStartF).toBe(50);
    expect(cut?.srcEndF).toBe(60);
  });

  it("emits no guard on a side with no timed neighbour (leading/trailing filler)", () => {
    const timed: TimedWord[] = [{ idx: 0, start: 64 / 30, end: 70 / 30 }]; // only a NEXT word
    const kept = fillerNeighborKept(filler(50, 60), timed, MEDIA, 30);
    expect(kept).toEqual([{ media: MEDIA, srcStartF: 64, srcEndF: 65 }]);
  });
});

describe("interleaveFillers — display interleave by time", () => {
  const w = (start: number, end: number, idx: number): { word: TranscriptWord; idx: number } =>
    ({ word: { w: "x", start, end, segIdx: 0 }, idx });

  it("places each filler before the first timed word whose start exceeds it, with a synthetic key", () => {
    const segWords = [w(1.0, 1.4, 0), w(2.0, 2.4, 1)];
    const fillers: TranscriptFiller[] = [{ w: "um", start: 1.6, end: 1.7, segIdx: 0 }];
    const tokens = interleaveFillers(segWords, fillers);
    expect(tokens.map((t) => (t.filler ? `f:${t.token.w}` : `w:${t.idx}`))).toEqual([
      "w:0",
      "f:um",
      "w:1",
    ]);
    const f = tokens[1];
    expect(f.filler).toBe(true);
    if (f.filler) expect(f.key).toBe("f:0:1.6"); // synthetic, never a word index
  });

  it("keeps word flat indices intact and trailing fillers at the paragraph tail", () => {
    const segWords = [w(1.0, 1.4, 5), w(2.0, 2.4, 6)];
    const fillers: TranscriptFiller[] = [{ w: "uh", start: 9.0, end: 9.1, segIdx: 0 }];
    const tokens = interleaveFillers(segWords, fillers);
    expect(tokens.map((t) => (t.filler ? "F" : t.idx))).toEqual([5, 6, "F"]);
  });

  it("sorts multiple fillers by start and never mints a word index for them", () => {
    const segWords = [w(1.0, 1.4, 0), w(3.0, 3.4, 1)];
    const fillers: TranscriptFiller[] = [
      { w: "b", start: 2.5, end: 2.6, segIdx: 0 },
      { w: "a", start: 1.5, end: 1.6, segIdx: 0 },
    ];
    const tokens = interleaveFillers(segWords, fillers);
    expect(tokens.map((t) => (t.filler ? t.token.w : "W"))).toEqual(["W", "a", "b", "W"]);
    // The word tokens still carry their original flat indices — the word space is untouched.
    expect(tokens.filter((t) => !t.filler).map((t) => (t.filler ? -1 : t.idx))).toEqual([0, 1]);
  });

  it("derives the same timed subsequence used for neighbour bounds", () => {
    // Sanity: timedWords over a small list is the input to fillerNeighborKept in the panel.
    const words: TranscriptWord[] = [
      { w: "a", start: 1, end: 1.5, segIdx: 0 },
      { w: "b", start: 2, end: 2.5, segIdx: 0 },
    ];
    expect(timedWords(words)).toEqual([
      { idx: 0, start: 1, end: 1.5 },
      { idx: 1, start: 2, end: 2.5 },
    ]);
  });
});

describe("batchCutLabel (the three arms)", () => {
  const cut = (kind: PendingCut["kind"], durSec = 1): PendingCut =>
    ({ media: MEDIA, srcStartF: 0, srcEndF: 30, kind, durSec });

  it("names an all-pause batch by count", () => {
    expect(batchCutLabel([cut("pause"), cut("pause")])).toBe("Removed 2 pauses");
    expect(batchCutLabel([cut("pause")])).toBe("Removed 1 pause");
  });

  it("names an all-filler batch by count", () => {
    expect(batchCutLabel([cut("filler"), cut("filler"), cut("filler")])).toBe("Removed 3 fillers");
    expect(batchCutLabel([cut("filler")])).toBe("Removed 1 filler");
  });

  it("falls back to the summed duration for a selection or a mixed batch", () => {
    expect(batchCutLabel([cut("selection", 1.5)])).toBe("Cut 1.5s");
    expect(batchCutLabel([cut("pause", 1.0), cut("filler", 0.5)])).toBe("Cut 1.5s");
    expect(batchCutLabel([])).toBe("Cut 0.0s");
  });
});
