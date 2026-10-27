// Preview/export parity for animated scenes: the TS preview's specs must match the expected specs the
// Python export produces for the shared fixture (tests/test_scene_specs_parity.py holds the export to
// the same file), so a scene animates at the same frames in the player and in the exported MP4. The
// preview resolves each stored shot (one casts the Garden profile) against the video's cast from
// cast_shot_parity.json, which holds that resolution to the export's.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { drawSceneFrame, duoBeat, shotTiming } from "motion-core";
import { nodeEnv, surface } from "../../motion-core/scripts/node-env.ts";
import type { TranscriptWord } from "../src/api";
import { castShot, parseCast } from "../src/v3/library/cast";
import type { Project, Shot } from "../src/types/project";
import { sceneSpans, speechMedia } from "../src/v3/story/model";
import { animatedSceneSpecs } from "../src/v3/story/shot/specs";

interface ExpectedSpec {
  sceneId: string;
  mode: string;
  shot: Shot;
  role: string;
  title: string;
  startF: number;
  durF: number;
  firstWordF: number | null;
  cueWordF: number | null;
}

const cast = parseCast(JSON.parse(readFileSync(resolve(__dirname, "../../tests/fixtures/cast_shot_parity.json"), "utf-8")).cast)!;
const fx = JSON.parse(readFileSync(resolve(__dirname, "../../tests/fixtures/scene_specs_parity.json"), "utf-8")) as {
  project: Project;
  words: TranscriptWord[];
  expected: ExpectedSpec[];
};

describe("scene render spec parity with the export", () => {
  it("draws the same scenes, at the same frames, with the same shot and timing", () => {
    const { project, words, expected } = fx;
    const media = speechMedia(project);
    const specs = animatedSceneSpecs(project, words, sceneSpans(project, media, words), media, cast);
    expect(specs.map((s) => s.sceneId)).toEqual(expected.map((e) => e.sceneId));
    specs.forEach((spec, i) => {
      const e = expected[i];
      expect(spec.mode).toBe(e.mode);
      expect([spec.startF, spec.durF]).toEqual([e.startF, e.durF]);
      expect(spec.timing).toEqual(shotTiming(e.startF, e.firstWordF, e.cueWordF, project.fps, e.durF));
      expect(spec.shot).toEqual(castShot(e.shot, e.role, e.title, cast));
    });
    expect(expected.some((e) => e.shot.character === "notes-guest")).toBe(true);
  });

  it("draws the duo scene's pair from the same resolved shot, its fist bump landing on the cue word's frame", () => {
    const { project, words, expected } = fx;
    const media = speechMedia(project);
    const duo = expected.find((e) => e.shot.costar)!;
    const spec = animatedSceneSpecs(project, words, sceneSpans(project, media, words), media, cast).find((s) => s.sceneId === duo.sceneId)!;
    expect(spec.shot.costar).toMatchObject({ character: "lens", expression: "grin", pose: "point", custom: { eyes: "arc", mouth: "smile" } });
    expect(spec.shot.interaction).toBe("fistbump");
    expect([spec.shot.eyebrow, spec.shot.accent]).toEqual(["FIELD NOTES", "#2bb3a3"]);
    const at = (f: number) => duoBeat("fistbump", (f - spec.startF) / project.fps, spec.timing.cueT!).lead.approach;
    expect([at(duo.cueWordF! - 1) < 1, at(duo.cueWordF!)]).toEqual([true, 1]);
  });

  it("springs the cued cameo in on its cue word's frame, the same frame the export renders it on", () => {
    const { project, words, expected } = fx;
    const media = speechMedia(project);
    const specs = animatedSceneSpecs(project, words, sceneSpans(project, media, words), media, cast);
    const cued = expected.find((e) => e.mode === "cameo" && e.cueWordF !== null)!;
    const spec = specs.find((s) => s.sceneId === cued.sceneId)!;
    expect(spec.timing.inT).toBe((cued.cueWordF! - cued.startF) / project.fps);
    const [W, H] = [480, 270];
    const ink = (f: number): number => {
      const { ctx } = surface(W, H);
      drawSceneFrame(ctx, "cameo", spec.shot, (f - spec.startF) / project.fps, spec.timing, W, H, nodeEnv());
      return ctx.getImageData(0, 0, W, H).data.filter((a, i) => i % 4 === 3 && a > 0).length;
    };
    expect([ink(spec.startF), ink(cued.cueWordF! - 1), ink(cued.cueWordF! + 15) > 200]).toEqual([0, 0, true]);
    const cut = expected.find((e) => e.mode === "cameo" && e.cueWordF === null)!;
    expect([cut.shot.cueWord !== undefined, specs.find((s) => s.sceneId === cut.sceneId)!.timing.inT]).toEqual([true, undefined]);
  });
});
