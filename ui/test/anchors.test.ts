import { describe, it, expect } from "vitest";
import type { Project } from "../src/types/project";
import type { TranscriptWord } from "../src/api";
import {
  nearestWordAnchor,
  flipAnchor,
  setElementAnchor,
  tetherWordIdx,
} from "../src/timeline/anchors";

const MEDIA = "s.mp4";

// Speech clip source [0,300) at tl 0 → srcToTimeline is identity. Two timed words: "a"@1.0s (srcF 30 →
// tl 30) and "b"@2.0s (srcF 60 → tl 60). One b-roll clip and one text item to anchor.
function fixture(): Project {
  return {
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
        clips: [{ id: "sp", media: { source: MEDIA, proxy: null }, srcInF: 0, srcOutF: 300, tlStartF: 0 }],
      },
      {
        id: "broll",
        kind: "video",
        clips: [{ id: "bv", media: { source: "b.mp4", proxy: null }, srcInF: 0, srcOutF: 40, tlStartF: 65 }],
      },
      {
        id: "txt",
        kind: "text",
        items: [
          { id: "t1", text: "hi", preset: "hook", pos: { x: 0.5, y: 0.2 }, tlStartF: 65, durF: 60 },
          { type: "graphic", id: "g1", componentId: "callout", componentVersion: 1, props: { label: "L", body: "B", accent: "#4cc9f0" }, pos: { x: 0.5, y: 0.5 }, scale: 1, tlStartF: 130, durF: 30 },
        ],
      },
    ],
  };
}

const words: TranscriptWord[] = [
  { w: "a", start: 1.0, end: 1.2, segIdx: 0 }, // tl 30
  { w: "b", start: 2.0, end: 2.2, segIdx: 0 }, // tl 60
];

describe("nearestWordAnchor", () => {
  it("picks the word whose timeline position is closest to the clip start and sets a keep-put offset", () => {
    const a = nearestWordAnchor(fixture(), words, 30, 65, MEDIA);
    expect(a).not.toBeNull();
    expect(a!.mode).toBe("word");
    expect(a!.ref).toEqual({ media: MEDIA, wordIdx: 1 }); // word "b" @ tl 60 is nearest to 65
    expect(a!.offsetF).toBe(5); // 65 - 60

    // Contract: covering.tlStartF + (wordFrame - covering.srcInF) + offsetF === clipTlStartF.
    const covering = fixture().tracks[0].clips![0];
    const wordFrame = 60;
    expect(covering.tlStartF + (wordFrame - covering.srcInF) + a!.offsetF!).toBe(65);
  });

  it("returns null when every word maps into a cut (no covering speech window)", () => {
    const p = fixture();
    p.tracks[0].clips![0].srcOutF = 10; // words at srcF 30/60 fall outside [0,10)
    expect(nearestWordAnchor(p, words, 30, 65, MEDIA)).toBeNull();
  });
});

describe("flipAnchor", () => {
  it("word → time", () => {
    const flipped = flipAnchor({ mode: "word", ref: { media: MEDIA, wordIdx: 1 }, offsetF: 5 }, fixture(), words, 30, 65, MEDIA);
    expect(flipped).toEqual({ mode: "time" });
  });

  it("time → word with the correct ref.media", () => {
    const flipped = flipAnchor({ mode: "time" }, fixture(), words, 30, 65, MEDIA);
    expect(flipped.mode).toBe("word");
    expect(flipped.ref?.media).toBe(MEDIA);
  });

  it("time → time when no word maps onto the timeline (fallback)", () => {
    const p = fixture();
    p.tracks[0].clips![0].srcOutF = 10;
    expect(flipAnchor({ mode: "time" }, p, words, 30, 65, MEDIA)).toEqual({ mode: "time" });
  });

  it("absent anchor → time", () => {
    expect(flipAnchor(undefined, fixture(), words, 30, 65, MEDIA)).toEqual({ mode: "time" });
  });
});

describe("setElementAnchor", () => {
  it("sets the anchor on a video clip immutably (input unchanged)", () => {
    const p = fixture();
    const next = setElementAnchor(p, "bv", { mode: "time" });
    expect(next).not.toBe(p);
    expect(next.tracks.find((t) => t.id === "broll")!.clips![0].anchor).toEqual({ mode: "time" });
    expect(p.tracks.find((t) => t.id === "broll")!.clips![0].anchor).toBeUndefined();
  });

  it("sets the anchor on a text item immutably (input unchanged)", () => {
    const p = fixture();
    const anchor = { mode: "word" as const, ref: { media: MEDIA, wordIdx: 0 }, offsetF: 0 };
    const next = setElementAnchor(p, "t1", anchor);
    expect(next.tracks.find((t) => t.id === "txt")!.items![0].anchor).toEqual(anchor);
    expect(p.tracks.find((t) => t.id === "txt")!.items![0].anchor).toBeUndefined();
  });

  it("sets the anchor on a graphic item immutably", () => {
    const p = fixture();
    const anchor = { mode: "word" as const, ref: { media: MEDIA, wordIdx: 1 }, offsetF: 0 };
    const next = setElementAnchor(p, "g1", anchor);
    expect(next.tracks.find((t) => t.id === "txt")!.items![1].anchor).toEqual(anchor);
    expect(p.tracks.find((t) => t.id === "txt")!.items![1].anchor).toBeUndefined();
  });
});

describe("tetherWordIdx", () => {
  it("returns ref.wordIdx for a word anchor", () => {
    expect(tetherWordIdx(fixture(), words, { mode: "word", ref: { media: MEDIA, wordIdx: 1 } })).toBe(1);
  });

  it("returns null for a time or absent anchor", () => {
    expect(tetherWordIdx(fixture(), words, { mode: "time" })).toBeNull();
    expect(tetherWordIdx(fixture(), words, undefined)).toBeNull();
  });
});
