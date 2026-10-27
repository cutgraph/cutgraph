// Museum travel example: scene timing and resolved shots match the export.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { PAN_S, panAt, resolveShot, shotTiming, type PartSpec, type ProjectShot } from "motion-core";
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

const fx = JSON.parse(readFileSync(resolve(__dirname, "../../tests/fixtures/scene_specs_parity_film.json"), "utf-8")) as {
  cast: unknown;
  project: Project;
  words: TranscriptWord[];
  expected: ExpectedSpec[];
  requests: { sceneId: string; shot: ProjectShot; custom: PartSpec | null }[];
};
const cast = parseCast(fx.cast)!;

describe("short-film staging parity with the export", () => {
  const media = speechMedia(fx.project);
  const specs = animatedSceneSpecs(fx.project, fx.words, sceneSpans(fx.project, media, fx.words), media, cast);

  it("draws the same scenes at the same frames, the staged footage scene left out", () => {
    expect(specs.map((s) => s.sceneId)).toEqual(fx.expected.map((e) => e.sceneId));
    specs.forEach((spec, i) => {
      const e = fx.expected[i];
      expect([spec.mode, spec.startF, spec.durF]).toEqual([e.mode, e.startF, e.durF]);
      expect(spec.timing).toEqual(shotTiming(e.startF, e.firstWordF, e.cueWordF, fx.project.fps, e.durF));
    });
  });

  it("resolves the travel features exactly as the Node renderer does from the export's request", () => {
    specs.forEach((spec, i) => {
      const e = fx.expected[i], req = fx.requests[i];
      const node = resolveShot(req.shot, e.role, e.title);
      expect(spec.shot).toEqual(req.custom ? { ...node, custom: req.custom } : node);
    });
    const shot = (id: string) => specs.find((s) => s.sceneId === id)!;
    expect(specs.map((s) => s.sceneId)).toEqual(["scene-1", "scene-2", "scene-3", "scene-4", "scene-5", "scene-6", "scene-7"]);
    expect(shot("scene-1").shot.badges).toEqual([{ text: "INDOORS", tone: "accent" }]);
    expect(shot("scene-2").shot.ensemble?.map((m) => m.pose)).toEqual(["point"]);
    expect(shot("scene-2").shot.costar?.bubble).toBe("say");
    expect(shot("scene-3").shot.boardLabels).toEqual(["Map", "Gallery", "Cafe"]);
    expect(fx.expected[2].cueWordF).toBeNull();
    expect(shot("scene-3").timing.cueT).toBe(shot("scene-3").timing.firstT);
    expect(shot("scene-4").shot.layout).toBe("screen");
    expect(shot("scene-4").shot.screen?.more).toBeUndefined();
    expect([shot("scene-5").shot.camera, shot("scene-5").shot.costar?.move]).toEqual(["pan", "idle"]);
    expect(shot("scene-6").shot.bubble).toBe("question");
    expect(shot("scene-7").shot.mark).toBe("TOUR");
  });

  it("gives the pan its scene length to settle in", () => {
    const walk = specs.find((s) => s.shot.camera === "pan")!;
    const pan = panAt(walk.shot.camera, 0, walk.timing.endT)!;
    expect(pan.dur).toBe(Math.min(PAN_S, walk.durF / fx.project.fps));
    expect(panAt(walk.shot.camera, pan.dur, walk.timing.endT)!.rest).toBe(0);
  });
});
