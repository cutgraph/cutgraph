// Each case owns a single scene; there is no shared story timeline.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { resolveShot, shotTiming, type PartSpec, type ProjectShot, type Shot } from "motion-core";
import type { TranscriptWord } from "../src/api";
import type { Project } from "../src/types/project";
import { parseCast } from "../src/v3/library/cast";
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

const fx = JSON.parse(readFileSync(resolve(__dirname, "../../tests/fixtures/scene_specs_parity_features.json"), "utf-8")) as {
  cast: unknown;
  words: TranscriptWord[];
  cases: { name: string; project: Project; expected: ExpectedSpec[];
    requests: { sceneId: string; shot: ProjectShot; custom: PartSpec | null }[] }[];
};

it("preserves every independent board, mark and member-label export contract in the preview", () => {
  const cast = parseCast(fx.cast)!;
  const shots: Record<string, Shot> = {};
  for (const c of fx.cases) {
    expect(c.project.scenes).toHaveLength(1);
    const media = speechMedia(c.project);
    const specs = animatedSceneSpecs(c.project, fx.words, sceneSpans(c.project, media, fx.words), media, cast);
    expect(specs).toHaveLength(1);
    const spec = specs[0], e = c.expected[0], req = c.requests[0];
    expect([spec.sceneId, spec.mode, spec.startF, spec.durF]).toEqual([e.sceneId, e.mode, e.startF, e.durF]);
    expect(req.sceneId).toBe(spec.sceneId);
    expect(spec.timing).toEqual(shotTiming(e.startF, e.firstWordF, e.cueWordF, c.project.fps, e.durF));
    const node = resolveShot(req.shot, e.role, e.title);
    expect(spec.shot).toEqual(req.custom ? { ...node, custom: req.custom } : node);
    shots[c.name] = spec.shot;
  }
  expect(Object.keys(shots).sort()).toEqual(["cameo-question", "chain-diagram", "chain-labels", "grid-diagram", "list-diagram", "list-partial-labels", "member-labels", "radial-diagram", "radial-question", "tree-diagram", "tree-labels"]);
  expect([shots["chain-labels"].diagram, shots["tree-labels"].diagram,
    shots["radial-question"].diagram, shots["list-partial-labels"].diagram]).toEqual(["chain", "tree", "radial", "list"]);
  expect(shots["chain-labels"].boardLabels).toEqual(["INDEX", "Files", "Pages", "Notes"]);
  expect(shots["tree-labels"].boardLabels).toEqual(["INDEX", "Files", "Pages", "Notes"]);
  expect(shots["list-partial-labels"].boardLabels).toEqual(["INDEX", "", "Pages"]);
  expect(shots["tree-labels"].costar?.bubble).toBe("exclaims");
  expect(shots["radial-question"].costar?.bubble).toBe("question");
  expect(fx.cases.find((c) => c.name === "cameo-question")!.expected[0].mode).toBe("cameo");
  expect(shots["cameo-question"].bubble).toBe("question");
  expect(shots["member-labels"].headlineAt).toBe("costar");
  expect(shots["member-labels"].ensemble?.map((m) => m.pose)).toEqual(["wave", "point", "think"]);
  expect(shots["member-labels"].ensemble?.map((m) => m.character)).toEqual(["lens", "lens", "lens"]);
  expect(shots["member-labels"].badges).toEqual([{ text: "HI", tone: "accent" }, { text: "7", tone: "ink" }, { text: "OAK", tone: "paper" }]);
});
