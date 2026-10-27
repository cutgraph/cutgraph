import { describe, expect, it } from "vitest";
import { resolveShot } from "motion-core";
import type { SceneRenderSpec } from "../src/preview/SceneLayer";
import { stillTime } from "../src/v3/story/shot/ScenePicture";

const spec = (durF: number, firstT: number, cueT: number): SceneRenderSpec => ({
  sceneId: "s1",
  mode: "animation",
  shot: resolveShot({ mode: "animation" }, "Hook", "Open"),
  timing: { firstT, cueT },
  startF: 0,
  durF,
});

describe("scene thumbnail still time", () => {
  it("shows the shot after its headline and diagram have landed", () => {
    expect(stillTime(spec(300, 0.5, 2), 30)).toBeCloseTo(3.2);
    expect(stillTime(spec(300, 0.5, 0.5), 30)).toBeCloseTo(1.7);
  });

  it("never runs past the end of a short scene", () => {
    expect(stillTime(spec(30, 0.5, 2), 30)).toBeCloseTo(0.9);
    expect(stillTime(spec(0, 0.5, 2), 30)).toBe(0);
  });
});
