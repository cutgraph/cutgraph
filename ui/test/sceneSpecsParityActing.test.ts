// Acting preview/export parity: the TS preview's specs for the acting fixture (bubbles on every
// kind of performer, ensembles with and without a co-star, members walking in, a cameo's "!") must match
// the export's, and each scene's resolved shot must be exactly what the Node renderer draws from the
// render request the export sends it (tests/test_scene_specs_parity_acting.py holds the export to the
// same file).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveShot, shotTiming, type PartSpec, type ProjectShot } from "motion-core";
import type { TranscriptWord } from "../src/api";
import { parseCast } from "../src/v3/library/cast";
import type { Project } from "../src/types/project";
import { sceneSpans, speechMedia } from "../src/v3/story/model";
import { animatedSceneSpecs } from "../src/v3/story/shot/specs";

interface ExpectedSpec {
  sceneId: string;
  mode: string;
  role: string;
  title: string;
  startF: number;
  durF: number;
  firstWordF: number | null;
  cueWordF: number | null;
}

const fx = JSON.parse(readFileSync(resolve(__dirname, "../../tests/fixtures/scene_specs_parity_acting.json"), "utf-8")) as {
  cast: unknown;
  project: Project;
  words: TranscriptWord[];
  expected: ExpectedSpec[];
  requests: { sceneId: string; shot: ProjectShot; custom: PartSpec | null }[];
};
const cast = parseCast(fx.cast)!;

describe("acting parity with the export", () => {
  const media = speechMedia(fx.project);
  const specs = animatedSceneSpecs(fx.project, fx.words, sceneSpans(fx.project, media, fx.words), media, cast);

  it("draws the same scenes at the same frames", () => {
    expect(specs.map((s) => s.sceneId)).toEqual(fx.expected.map((e) => e.sceneId));
    specs.forEach((spec, i) => {
      const e = fx.expected[i];
      expect([spec.mode, spec.startF, spec.durF]).toEqual([e.mode, e.startF, e.durF]);
      expect(spec.timing).toEqual(shotTiming(e.startF, e.firstWordF, e.cueWordF, fx.project.fps, e.durF));
    });
  });

  it("resolves every performer, bubble and member exactly as the Node renderer does from the export's request", () => {
    specs.forEach((spec, i) => {
      const e = fx.expected[i], req = fx.requests[i];
      const node = resolveShot(req.shot, e.role, e.title);
      expect(spec.shot).toEqual(req.custom ? { ...node, custom: req.custom } : node);
    });
    const duo = specs.find((s) => s.sceneId === "scene-1")!.shot;
    expect([duo.bubble, duo.costar?.bubble, duo.ensemble?.map((m) => m.bubble)]).toEqual(["think", "exclaim", ["say", undefined]]);
    expect(duo.ensemble?.map((m) => [m.character, m.palette, m.move])).toEqual([["lens", "periwinkle", "sway"], ["lens", "periwinkle", "enter"]]);
    expect(duo.costar?.custom).toEqual({ eyes: "narrow", mouth: "round" });
    const line = specs.find((s) => s.sceneId === "scene-3")!.shot;
    expect([line.costar, line.ensemble?.length, line.ensemble?.[1].character]).toEqual([undefined, 2, "lens"]);
    const missing = (id: string) => specs.find((s) => s.sceneId === `missing-${id}`)!.shot;
    for (const look of [missing("lead"), missing("costar").costar!, missing("ensemble").ensemble![0], missing("cameo")]) {
      expect(look.character).toBe("retired-rig");
      expect(look.custom).toBeUndefined();
    }
    expect(missing("costar").interaction).toBe("face");
    expect(missing("ensemble").ensemble![0].move).toBe("enter");
  });
});
