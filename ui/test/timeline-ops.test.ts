import { describe, it, expect } from "vitest";
import type { Project } from "../src/types/project";
import type { TranscriptWord } from "../src/api";
import {
  moveClip,
  moveElementToTrack,
  canPlaceOnTrack,
  trimClip,
  splitClipAt,
  deleteElement,
  nudgeClip,
  snapFrame,
} from "../src/timeline/ops";

// A fixture project: one speech clip (source [0,300) at tl 0), one b-roll clip (tl 100), one text item.
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
        clips: [{ id: "sp", media: { source: "s.mp4", proxy: null }, srcInF: 0, srcOutF: 300, tlStartF: 0 }],
      },
      {
        id: "broll",
        kind: "video",
        clips: [{ id: "bv", media: { source: "b.mp4", proxy: null }, srcInF: 0, srcOutF: 40, tlStartF: 100 }],
      },
      {
        id: "txt",
        kind: "text",
        items: [{ id: "t1", text: "hi", preset: "hook", pos: { x: 0.5, y: 0.2 }, tlStartF: 200, durF: 60 }],
      },
    ],
  };
}

// Per-track: sorted by tlStartF, no clip starts before the previous one ends.
function validateNoOverlap(p: Project): void {
  for (const t of p.tracks) {
    const clips = [...(t.clips ?? [])].sort((a, b) => a.tlStartF - b.tlStartF);
    for (let i = 1; i < clips.length; i++) {
      const prevEnd = clips[i - 1].tlStartF + (clips[i - 1].srcOutF - clips[i - 1].srcInF);
      expect(clips[i].tlStartF).toBeGreaterThanOrEqual(prevEnd);
    }
  }
}

function broll(p: Project) {
  return p.tracks.find((t) => t.id === "broll")!.clips![0];
}

describe("moveClip", () => {
  it("clamps ≥0 and returns a new object without mutating the input", () => {
    const p = fixture();
    const moved = moveClip(p, "bv", -50);
    expect(moved).not.toBe(p);
    expect(broll(moved).tlStartF).toBe(0);
    expect(broll(p).tlStartF).toBe(100); // input untouched
    validateNoOverlap(moved);
  });

  it("moves a b-roll clip to a positive frame", () => {
    const moved = moveClip(fixture(), "bv", 250);
    expect(broll(moved).tlStartF).toBe(250);
  });
});

describe("cross-track moves", () => {
  function tiered(): Project {
    return {
      ...fixture(),
      tracks: [
        ...fixture().tracks,
        { id: "txt_lower", kind: "text", items: [] },
        { id: "broll_lower", kind: "video", clips: [] },
      ],
    };
  }

  it("moves a title to another text tier at its requested time", () => {
    const moved = moveElementToTrack(tiered(), "t1", "txt_lower", 210);
    expect(moved.tracks.find((track) => track.id === "txt")!.items).toEqual([]);
    expect(moved.tracks.find((track) => track.id === "txt_lower")!.items![0]).toMatchObject({
      id: "t1", tlStartF: 210, durF: 60,
    });
  });

  it("rejects an occupied tier and an incompatible track without changing the project", () => {
    const p = tiered();
    p.tracks.find((track) => track.id === "txt_lower")!.items = [
      { id: "occupied", text: "no", preset: "hook", pos: { x: 0.5, y: 0.2 }, tlStartF: 210, durF: 60 },
    ];
    expect(moveElementToTrack(p, "t1", "txt_lower", 210)).toBe(p);
    expect(moveElementToTrack(p, "t1", "broll", 210)).toBe(p);
  });

  it("moves a placed video clip to another video row at its requested time", () => {
    const moved = moveElementToTrack(tiered(), "bv", "broll_lower", 210);
    expect(moved.tracks.find((track) => track.id === "broll")!.clips).toEqual([]);
    expect(moved.tracks.find((track) => track.id === "broll_lower")!.clips![0]).toMatchObject({
      id: "bv", tlStartF: 210, srcInF: 0, srcOutF: 40,
    });
  });

  it("moves a placed audio clip to another audio row at its requested time", () => {
    const p: Project = {
      ...tiered(),
      tracks: [
        ...tiered().tracks,
        { id: "music", kind: "audio", clips: [
          { id: "au", media: { source: "music.mp3", proxy: null }, srcInF: 0, srcOutF: 90, tlStartF: 20 },
        ] },
        { id: "music_lower", kind: "audio", clips: [] },
      ],
    };
    const moved = moveElementToTrack(p, "au", "music_lower", 210);
    expect(moved.tracks.find((track) => track.id === "music")!.clips).toEqual([]);
    expect(moved.tracks.find((track) => track.id === "music_lower")!.clips![0]).toMatchObject({
      id: "au", tlStartF: 210, srcInF: 0, srcOutF: 90,
    });
  });

  it("rejects a cross-row media collision without changing the project", () => {
    const p = tiered();
    p.tracks.find((track) => track.id === "broll_lower")!.clips = [
      { id: "occupied", media: { source: "other.mp4", proxy: null }, srcInF: 0, srcOutF: 40, tlStartF: 210 },
    ];
    expect(moveElementToTrack(p, "bv", "broll_lower", 210)).toBe(p);
  });

  it("accepts an open compatible media span and rejects a collision", () => {
    const p = fixture();
    expect(canPlaceOnTrack(p, "broll", "video", 0, 40)).toBe(true);
    expect(canPlaceOnTrack(p, "broll", "video", 110, 40)).toBe(false);
    expect(canPlaceOnTrack(p, "txt", "video", 0, 40)).toBe(false);
  });
});

describe("trimClip", () => {
  it("edge 'in' shifts tlStartF by the source delta and keeps srcOutF>srcInF", () => {
    // srcInF 0→10 (delta +10) shifts tlStartF 100→110; source under the edge stays fixed.
    const trimmed = trimClip(fixture(), "bv", "in", 10);
    const c = broll(trimmed);
    expect(c.srcInF).toBe(10);
    expect(c.tlStartF).toBe(110);
    expect(c.srcOutF).toBeGreaterThan(c.srcInF);
    validateNoOverlap(trimmed);
  });

  it("edge 'in' cannot cross srcOutF (min 1-frame clip)", () => {
    const c = broll(trimClip(fixture(), "bv", "in", 999));
    expect(c.srcInF).toBe(c.srcOutF - 1);
  });

  it("edge 'out' sets srcOutF and enforces >srcInF", () => {
    const c = broll(trimClip(fixture(), "bv", "out", 20));
    expect(c.srcOutF).toBe(20);
    const c2 = broll(trimClip(fixture(), "bv", "out", -5));
    expect(c2.srcOutF).toBe(c2.srcInF + 1);
  });
});

describe("splitClipAt", () => {
  it("yields two contiguous seamless clips whose union covers the original", () => {
    const p = fixture();
    const split = splitClipAt(p, "bv", 25);
    const clips = split.tracks.find((t) => t.id === "broll")!.clips!;
    expect(clips.length).toBe(2);
    const [left, right] = clips;
    expect(left.id).toBe("bv");
    expect(right.id).toBe("bv-s25");
    expect(left.srcInF).toBe(0);
    expect(left.srcOutF).toBe(25);
    expect(right.srcInF).toBe(25);
    expect(right.srcOutF).toBe(40);
    // contiguous on the timeline: left ends exactly where right begins.
    expect(right.tlStartF).toBe(left.tlStartF + (left.srcOutF - left.srcInF));
    // union of source covers the original [0,40); no seams introduced.
    expect(left.seams).toBeUndefined();
    expect(right.seams).toBeUndefined();
    validateNoOverlap(split);
  });

  it("is a no-op when the split point is at or outside the source window", () => {
    const p = fixture();
    expect(splitClipAt(p, "bv", 0).tracks.find((t) => t.id === "broll")!.clips!.length).toBe(1);
    expect(splitClipAt(p, "bv", 40).tracks.find((t) => t.id === "broll")!.clips!.length).toBe(1);
  });
});

describe("deleteElement", () => {
  it("removes a clip by id", () => {
    const del = deleteElement(fixture(), "bv");
    expect(del.tracks.find((t) => t.id === "broll")!.clips!.length).toBe(0);
  });

  it("removes a text item by id", () => {
    const del = deleteElement(fixture(), "t1");
    expect(del.tracks.find((t) => t.id === "txt")!.items!.length).toBe(0);
  });
});

describe("nudgeClip", () => {
  it("shifts tlStartF by delta and clamps ≥0", () => {
    expect(broll(nudgeClip(fixture(), "bv", 10)).tlStartF).toBe(110);
    expect(broll(nudgeClip(fixture(), "bv", -500)).tlStartF).toBe(0);
  });
});

// A video track with TWO clips so the per-track neighbor clamp actually has a neighbor to
// clamp against — the single-clip fixture above can never exercise the overlap guard.
//   bv1 = [tl 0, 40)   bv2 = [tl 60, 100)  (bv2 source window is [30,70), dur 40)
function multi(): Project {
  return {
    fps: 30,
    width: 1920,
    height: 1080,
    name: "p",
    revision: 1,
    schemaVersion: 1,
    tracks: [
      {
        id: "broll",
        kind: "video",
        clips: [
          { id: "bv1", media: { source: "b.mp4", proxy: null }, srcInF: 0, srcOutF: 40, tlStartF: 0 },
          { id: "bv2", media: { source: "b.mp4", proxy: null }, srcInF: 30, srcOutF: 70, tlStartF: 60 },
        ],
      },
    ],
  };
}

function clip(p: Project, id: string) {
  return p.tracks[0].clips!.find((c) => c.id === id)!;
}

describe("neighbor overlap clamps (valid by construction)", () => {
  it("moveClip: a drag into the left neighbor stops at its edge", () => {
    // bv2 dragged left to 20 would overlap bv1 [0,40); it comes to rest butting bv1's end (40).
    const moved = moveClip(multi(), "bv2", 20);
    expect(clip(moved, "bv2").tlStartF).toBe(40);
    validateNoOverlap(moved);
  });

  it("moveClip: a drag into the right neighbor stops so the clip ends at its start", () => {
    // bv1 (dur 40) dragged right to 90 would overlap bv2 @60; it stops at 20 so it ends exactly at 60.
    const moved = moveClip(multi(), "bv1", 90);
    expect(clip(moved, "bv1").tlStartF).toBe(20);
    validateNoOverlap(moved);
  });

  it("nudgeClip: a nudge PAST a neighbor edge is identity (returns the input project)", () => {
    const p = multi();
    expect(nudgeClip(p, "bv2", -80)).toBe(p); // desired 0 is left of bv1's end (40) → no-op
    validateNoOverlap(nudgeClip(p, "bv2", -80));
  });

  it("nudgeClip: a nudge that still fits moves normally", () => {
    const nudged = nudgeClip(multi(), "bv2", -20); // 60 → 40, exactly butting bv1
    expect(clip(nudged, "bv2").tlStartF).toBe(40);
    validateNoOverlap(nudged);
  });

  it("trimClip 'out': extending the end cannot cross the right neighbor's start", () => {
    const trimmed = trimClip(multi(), "bv1", "out", 200); // capped so bv1 ends at bv2's start (60)
    expect(clip(trimmed, "bv1").srcOutF).toBe(60);
    validateNoOverlap(trimmed);
  });

  it("trimClip 'in': exposing more head cannot push tlStartF under the left neighbor's end", () => {
    const trimmed = trimClip(multi(), "bv2", "in", 0); // clamped so bv2 starts at bv1's end (40)
    const c = clip(trimmed, "bv2");
    expect(c.srcInF).toBe(10);
    expect(c.tlStartF).toBe(40);
    validateNoOverlap(trimmed);
  });
});

describe("snapFrame", () => {
  const words: TranscriptWord[] = [
    { w: "a", start: 1.0, end: 1.2, segIdx: 0 }, // 1.0s → srcF 30 → tl 30
    { w: "b", start: 2.0, end: 2.2, segIdx: 0 }, // tl 60
  ];

  it("snaps to a word boundary within threshold", () => {
    // candidate 32 is 2 frames (2px at 1px/frame) from word tl 30 → snaps to 30.
    expect(snapFrame(fixture(), words, 30, 32, 1, 5, "bv")).toBe(30);
  });

  it("snaps to a neighbouring clip edge", () => {
    // the b-roll clip (excluded) starts at 100; from a text-item drag, candidate 98 → snaps to 100.
    expect(snapFrame(fixture(), words, 30, 98, 1, 5, "t1")).toBe(100);
  });

  it("returns the candidate unchanged when nothing is within threshold", () => {
    expect(snapFrame(fixture(), words, 30, 500, 1, 5, "bv")).toBe(500);
  });
});
