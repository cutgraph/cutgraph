// Preview/export parity for short-film staging: the TS preview's specs must match the staging
// fixture's expected specs, which tests/test_scene_specs_parity_staging.py holds the export to, and a
// `screen` shot's library images (phone 1, then phones 2 and 3) must resolve to the same assets on
// both sides: the video's workspace library first, then the brand kit, images only (the export resolves
// each to that file, the preview to its URL).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { cameraAt, resolveShot, shotTiming, stagingOf } from "motion-core";
import type { TranscriptWord } from "../src/api";
import type { Asset } from "../src/v3/api";
import { castShot, NO_CAST } from "../src/v3/library/cast";
import type { Project, Shot } from "../src/types/project";
import type { LibraryAsset } from "../src/v3/story/drawer/library";
import { sceneSpans, speechMedia } from "../src/v3/story/model";
import { librariesOf, withScreenImage } from "../src/v3/story/shot/screenLibrary";
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

const fx = JSON.parse(readFileSync(resolve(__dirname, "../../tests/fixtures/scene_specs_parity_staging.json"), "utf-8")) as {
  words: TranscriptWord[];
  library: { workspaceId: string; asset: Pick<Asset, "id" | "name" | "kind" | "file"> }[];
  cases: StagingCase[];
};
interface StagingCase {
  name: string;
  project: Project;
  images: Record<string, string | null>;
  moreImages: Record<string, (string | null)[]>;
  expected: ExpectedSpec[];
}

const LIBRARY: LibraryAsset[] = fx.library.map((e) => ({
  workspaceId: e.workspaceId, workspaceName: e.workspaceId, asset: { ...e.asset, sha256: "", bytes: 0, tags: [], createdAt: "" },
}));

function specs(c: StagingCase) {
  const { project } = c, { words } = fx;
  const media = speechMedia(project);
  return animatedSceneSpecs(project, words, sceneSpans(project, media, words), media, NO_CAST, LIBRARY);
}

describe("staging spec parity with the export", () => {
  it("draws the same scenes, at the same frames, with the same staged shot, timing and scene length", () => {
    for (const c of fx.cases) {
      const { project, expected } = c;
      expect(project.scenes).toHaveLength(1);
      const got = specs(c);
      expect(got.map((s) => s.sceneId)).toEqual(expected.map((e) => e.sceneId));
      got.forEach((spec, i) => {
        const e = expected[i];
        expect([spec.mode, spec.startF, spec.durF]).toEqual([e.mode, e.startF, e.durF]);
        expect(spec.timing).toEqual(shotTiming(e.startF, e.firstWordF, e.cueWordF, project.fps, e.durF));
        expect(spec.shot).toEqual(withScreenImage(castShot(e.shot, e.role, e.title, NO_CAST), LIBRARY));
        expect(spec.shot).toMatchObject(stagingOf(e.shot));
      });
    }
    expect(specs(fx.cases.find((c) => c.name === "staged-footage")!)).toEqual([]);
  });

  it("resolves each screen's image to the asset the export renders: the workspace's, then the brand kit's, images only", () => {
    const ws = fx.cases[0].project.workspaceId!;
    expect(librariesOf(ws)).toEqual([ws, "brand"]);
    expect(librariesOf(null)).toEqual(["brand"]);
    const phones: number[] = [];
    for (const c of fx.cases) for (const spec of specs(c).filter((s) => s.shot.layout === "screen")) {
      phones.push(1 + (spec.shot.screen?.more?.length ?? 0));
      const want = c.images[spec.sceneId];
      expect(spec.shot.screen?.image, spec.sceneId).toBe(want ? `/media/workspace/${want}` : undefined);
      const more = c.moreImages[spec.sceneId] ?? [];
      expect(spec.shot.screen?.more?.map((m) => m.image) ?? [], `${spec.sceneId}: phones 2 and 3`).toEqual(more.map((m) => (m ? `/media/workspace/${m}` : undefined)));
    }
    expect(phones.sort()).toEqual([1, 2, 3]);
    expect(fx.cases.some((c) => Object.values(c.images).includes(null))).toBe(true);
    expect(fx.cases.some((c) => Object.values(c.moreImages).flat().includes(null))).toBe(true);
    const alone = withScreenImage(resolveShot({ mode: "animation", layout: "screen", screen: { asset: "a1b2c3d4e5f60718" } }, "Beat", "x"), LIBRARY.filter((e) => e.workspaceId === "brand"));
    expect(alone.screen?.image).toBe("/media/workspace/brand/a1b2c3d4e5f60718.jpeg");
  });

  it("gives bounded cameras the scene length and independent visible endpoints", () => {
    const got = fx.cases.flatMap(specs);
    const push = got.find((s) => s.shot.camera === "push-in")!, pull = got.find((s) => s.shot.camera === "pull-out")!;
    const last = (push.durF - 1) / fx.cases[0].project.fps;
    expect(push.timing.endT).toBeGreaterThan(0);
    expect(pull.timing.endT).toBeGreaterThan(0);
    expect(cameraAt(push.shot.camera, 0, push.timing.endT)).toEqual({ k: 1 });
    expect(cameraAt(push.shot.camera, last, push.timing.endT).k).toBeGreaterThan(1.17);
    expect(cameraAt(push.shot.camera, push.timing.endT!, push.timing.endT)).toEqual({ k: 1.18 });
    expect(cameraAt(pull.shot.camera, 0, pull.timing.endT)).toEqual({ k: 1.12 });
    expect(cameraAt(pull.shot.camera, pull.timing.endT!, pull.timing.endT)).toEqual({ k: 1 });
  });
});
