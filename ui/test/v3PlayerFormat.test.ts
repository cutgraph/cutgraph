import { describe, expect, it } from "vitest";
import { applyFormat, formatOf } from "../src/v3/story/player/format";
import type { Project } from "../src/types/project";

function project(overrides: Partial<Project> = {}): Project {
  return { schemaVersion: 1, name: "p", revision: 1, fps: 30, width: 1920, height: 1080, tracks: [], ...overrides };
}

describe("formatOf — reads the named aspect from width/height", () => {
  it("recognizes every FORMATS entry", () => {
    expect(formatOf(project({ width: 1920, height: 1080 }))).toBe("16:9");
    expect(formatOf(project({ width: 1080, height: 1920 }))).toBe("9:16");
    expect(formatOf(project({ width: 1080, height: 1080 }))).toBe("1:1");
  });

  it("falls back to 16:9 for dimensions that match no known format", () => {
    expect(formatOf(project({ width: 640, height: 480 }))).toBe("16:9");
  });
});

describe("applyFormat — writes width/height/crop for the target format", () => {
  it("sets the FORMATS dimensions for every target", () => {
    expect(applyFormat(project(), "9:16")).toMatchObject({ width: 1080, height: 1920 });
    expect(applyFormat(project(), "1:1")).toMatchObject({ width: 1080, height: 1080 });
    expect(applyFormat(project({ width: 1080, height: 1920 }), "16:9")).toMatchObject({ width: 1920, height: 1080 });
  });

  it("clears crop for 16:9 — an absent crop skips compile.py's crop filter entirely", () => {
    const p = project({ width: 1080, height: 1920, crop: { mode: "center" } });
    expect(applyFormat(p, "16:9").crop).toBeNull();
  });

  it("falls back to a static center crop for a shorts format with no known reframe metadata", () => {
    expect(applyFormat(project(), "9:16").crop).toEqual({ mode: "center" });
    expect(applyFormat(project(), "1:1").crop).toEqual({ mode: "center" });
  });

  it("reframes a shorts format when reframe metadata is already known (crop.metaPath)", () => {
    const p = project({ crop: { mode: "center", metaPath: "outputs/reframe/a.reframe.json" } });
    expect(applyFormat(p, "9:16").crop).toEqual({ mode: "reframe", metaPath: "outputs/reframe/a.reframe.json" });
  });

  it("does not mutate the input project", () => {
    const p = project();
    const next = applyFormat(p, "9:16");
    expect(p.width).toBe(1920);
    expect(next).not.toBe(p);
  });

  it("round-trips through formatOf for every format", () => {
    for (const f of ["16:9", "9:16", "1:1"] as const) {
      expect(formatOf(applyFormat(project(), f))).toBe(f);
    }
  });
});
