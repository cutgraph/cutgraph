import { describe, it, expect } from "vitest";
import { summarizeOps } from "../src/chat/opSummary";
import type { Project } from "../src/types/project";

// A speech clip c1 @0 (dur 300 = 10s @30fps), a b-roll clip b1 on a video track, and a text item x1.
function project(): Project {
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
        clips: [
          { id: "c1", media: { source: "s.mp4", proxy: null }, srcInF: 0, srcOutF: 300, tlStartF: 0 },
          { id: "c4", media: { source: "s.mp4", proxy: null }, srcInF: 0, srcOutF: 90, tlStartF: 300 },
        ],
      },
      {
        id: "broll",
        kind: "video",
        clips: [{ id: "b1", media: { source: "b.mp4", proxy: null }, srcInF: 0, srcOutF: 60, tlStartF: 120 }],
      },
      {
        id: "text",
        kind: "text",
        items: [{ id: "x1", text: "Hook", preset: "lower", pos: { x: 0, y: 0 }, durF: 60, tlStartF: 30 }],
      },
    ],
  };
}

describe("summarizeOps — RFC-6902 path → human phrase", () => {
  const p = project();
  const fps = 30;

  it("a remove of a whole clip reads as a cut over its timeline span (mm:ss)", () => {
    // c1 spans tlStartF 0 → 0 + (300-0) = 300 frames = 0:00–0:10 @30fps.
    const s = summarizeOps([{ op: "remove", path: "/tracks/0/clips/0" }], p, fps);
    expect(s).toBe("cut 0:00–0:10");
  });

  it("a srcOutF replace reads as a trim of the resolved clip id", () => {
    const s = summarizeOps(
      [
        { op: "test", path: "/tracks/0/clips/1/id", value: "c4" },
        { op: "replace", path: "/tracks/0/clips/1/srcOutF", value: 75 },
      ],
      p,
      fps,
    );
    expect(s).toBe("trim clip c4"); // the guard is skipped; only the trim shows
  });

  it("a tlStartF replace on a video track reads as a b-roll move", () => {
    const s = summarizeOps([{ op: "replace", path: "/tracks/1/clips/0/tlStartF", value: 200 }], p, fps);
    expect(s).toBe("move b-roll b1");
  });

  it("an item add reads as add text; a text tlStartF move resolves the item id", () => {
    expect(summarizeOps([{ op: "add", path: "/tracks/2/items/-", value: {} }], p, fps)).toBe("add text");
    expect(summarizeOps([{ op: "replace", path: "/tracks/2/items/0/tlStartF", value: 90 }], p, fps)).toBe(
      "move text x1",
    );
  });

  it("dedupes and joins multiple ops with ' · '", () => {
    const s = summarizeOps(
      [
        { op: "remove", path: "/tracks/0/clips/0" },
        { op: "test", path: "/tracks/0/clips/1/id", value: "c4" },
        { op: "replace", path: "/tracks/0/clips/1/srcOutF", value: 75 },
        { op: "replace", path: "/tracks/1/clips/0/tlStartF", value: 200 },
      ],
      p,
      fps,
    );
    expect(s).toBe("cut 0:00–0:10 · trim clip c4 · move b-roll b1");
  });

  it("falls back gracefully when the project is null or the path is off-model", () => {
    expect(summarizeOps([{ op: "remove", path: "/tracks/0/clips/0" }], null, fps)).toBe("cut clip");
    expect(summarizeOps([{ op: "replace", path: "/width", value: 1280 }], p, fps)).toBe("edit width");
    expect(summarizeOps([], p, fps)).toBe("no changes");
  });
});
