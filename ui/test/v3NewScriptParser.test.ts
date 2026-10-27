// The sheet's live script -> scenes preview and its estimated length. The golden fixture
// is shared with tests/test_script_video.py, so the preview and the video the server creates agree.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { timeline, wordDur } from "motion-core";
import { estimate, parseScript, SAMPLE_SCRIPT, type ScriptScene } from "../src/v3/newscript/script";

interface GoldenCase {
  name: string;
  script: string;
  scenes: ScriptScene[];
  timings: [number, number][];
  duration: number;
}

const golden = JSON.parse(
  readFileSync(new URL("../../tests/fixtures/script_video_golden.json", import.meta.url), "utf-8"),
) as { cases: GoldenCase[] };

describe("parseScript (golden, shared with the Python parser)", () => {
  it.each(golden.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(parseScript(c.script)).toEqual(c.scenes);
  });
});

describe("estimated timing is motion-core's timeline", () => {
  it.each(golden.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const TL = timeline({ scenes: c.scenes.map((s) => ({ words: s.words.map((w) => ({ w })), cues: [] })) });
    expect(TL.words.map((w) => [w.start, w.end])).toEqual(c.timings);
    expect(TL.dur).toBe(c.duration);
    const est = estimate(c.scenes);
    expect(est.total).toBe(c.duration);
    expect(est.scenes.reduce((a, b) => a + b, 0)).toBeCloseTo(c.duration, 9);
  });

  it("gives each scene its lead-in, its words and its tail", () => {
    const [scene] = parseScript("## One\nHello there.");
    expect(estimate([scene]).scenes[0]).toBeCloseTo(0.45 + wordDur("Hello") + wordDur("there.") + 0.55, 9);
  });
});

describe("the sample script", () => {
  it("is a six-scene gardening story with named roles", () => {
    expect(parseScript(SAMPLE_SCRIPT).map((s) => [s.role, s.title])).toEqual([
      ["Hook", "A little green space"],
      ["Problem", "Too much choice"],
      ["Approach", "Start small"],
      ["Build", "Soil and water"],
      ["Check", "Watch the leaves"],
      ["Payoff", "Fresh herbs"],
    ]);
  });
});
