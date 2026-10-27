import { describe, expect, it } from "vitest";
import type { Project } from "../src/types/project";
import { buildDerivedProject, cropForDerived, planRenderFor, workingCaptionsPatch } from "../src/v3/story/export/derivedDoc";
import { derivedProjectName, formatAspect, formatSize, formatSlug, isWorkingFormat, matchingFormat, renderProjectName } from "../src/v3/story/export/formats";

function project(overrides: Partial<Project> = {}): Project {
  return {
    name: "demo-talk", fps: 30, width: 1920, height: 1080, revision: 2, schemaVersion: 3,
    tracks: [{ id: "speech", kind: "speech", clips: [{ id: "s0", media: { source: "data/source/demo-talk.mp4" }, srcInF: 0, srcOutF: 687, tlStartF: 0 }] }],
    scenes: [{ id: "scene-1", title: "Hook", role: "Hook", startWord: 0 }],
    ...overrides,
  };
}

describe("export format catalogue", () => {
  it("slugs a format for the compound doc name", () => {
    expect(formatSlug("9:16")).toBe("9x16");
    expect(formatSlug("16:9")).toBe("16x9");
    expect(formatSlug("1:1")).toBe("1x1");
    expect(derivedProjectName("demo-talk", "9:16")).toBe("demo-talk__9x16");
  });

  it("makes 4K the 16:9 frame at twice its size, named by that size", () => {
    expect(formatSize("4K")).toEqual([3840, 2160]);
    expect(formatSize("16:9")).toEqual([1920, 1080]);
    expect(formatAspect("4K")).toBe("16:9");
    expect(formatSlug("4K")).toBe("3840x2160");
    expect(derivedProjectName("demo-talk", "4K")).toBe("demo-talk__3840x2160");
    expect(renderProjectName("demo-talk", project(), "4K")).toBe("demo-talk__3840x2160");
    expect(matchingFormat({ width: 1920, height: 1080 })).toBe("16:9");
  });

  it("matches the working doc's own dimensions to a format", () => {
    expect(matchingFormat({ width: 1920, height: 1080 })).toBe("16:9");
    expect(matchingFormat({ width: 1080, height: 1920 })).toBe("9:16");
    expect(matchingFormat({ width: 1080, height: 1080 })).toBe("1:1");
    expect(matchingFormat({ width: 640, height: 480 })).toBeNull();
  });

  it("names the render project: the working doc itself when the format already matches", () => {
    const p = project();
    expect(isWorkingFormat(p, "16:9")).toBe(true);
    expect(isWorkingFormat(p, "9:16")).toBe(false);
    expect(renderProjectName("demo-talk", p, "16:9")).toBe("demo-talk");
    expect(renderProjectName("demo-talk", p, "9:16")).toBe("demo-talk__9x16");
    expect(renderProjectName("demo-talk", p, "1:1")).toBe("demo-talk__1x1");
  });
});

describe("derived render doc construction", () => {
  it("plans a same-dims format as the working doc, no derived copy", () => {
    const plan = planRenderFor(project(), "16:9", true);
    expect(plan).toEqual({ project: "demo-talk", derived: null });
  });

  it("builds a derived doc with the format's own dimensions", () => {
    const derived = buildDerivedProject(project(), "9:16", true);
    expect(derived.name).toBe("demo-talk__9x16");
    expect(derived.width).toBe(1080);
    expect(derived.height).toBe(1920);
    // Everything else is carried over from the working doc verbatim.
    expect(derived.tracks).toEqual(project().tracks);
    expect(derived.scenes).toEqual(project().scenes);
    expect(derived.fps).toBe(30);
  });

  it("builds a 4K derived doc: the working doc at 3840x2160, framed as it is", () => {
    const derived = buildDerivedProject(project(), "4K", true);
    expect(derived).toEqual({ ...project(), name: "demo-talk__3840x2160", width: 3840, height: 2160, captions: { enabled: true } });
    expect("crop" in derived).toBe(false);
    const reframed = project({ crop: { mode: "reframe", metaPath: "outputs/reframe/demo-talk.reframe.json" } });
    expect(buildDerivedProject(reframed, "4K", true).crop).toEqual(reframed.crop);
  });

  it("reframes a vertical working doc to fill a 4K frame, as it would for 16:9", () => {
    const vertical = project({ width: 1080, height: 1920 });
    expect(buildDerivedProject(vertical, "4K", true).crop).toEqual({ mode: "center" });
    expect(buildDerivedProject(vertical, "16:9", true).crop).toEqual({ mode: "center" });
  });

  it("defaults an un-reframed working doc to a center crop", () => {
    expect(cropForDerived(project())).toEqual({ mode: "center" });
    const derived = buildDerivedProject(project(), "1:1", false);
    expect(derived.crop).toEqual({ mode: "center" });
  });

  it("carries the working doc's own reframe metaPath into the derived crop", () => {
    const p = project({ crop: { mode: "reframe", metaPath: "outputs/reframe/demo-talk.reframe.json" } });
    expect(cropForDerived(p)).toEqual({ mode: "reframe", metaPath: "outputs/reframe/demo-talk.reframe.json" });
    const derived = buildDerivedProject(p, "9:16", true);
    expect(derived.crop).toEqual({ mode: "reframe", metaPath: "outputs/reframe/demo-talk.reframe.json" });
  });

  it("sets captions.enabled on the derived doc per the toggle", () => {
    expect(buildDerivedProject(project(), "9:16", true).captions).toEqual({ enabled: true });
    expect(buildDerivedProject(project(), "9:16", false).captions).toEqual({ enabled: false });
  });

  it("plans a full derived doc for a non-matching format, captions included", () => {
    const plan = planRenderFor(project(), "9:16", true);
    expect(plan.project).toBe("demo-talk__9x16");
    expect(plan.derived?.width).toBe(1080);
    expect(plan.derived?.height).toBe(1920);
    expect(plan.derived?.captions).toEqual({ enabled: true });
  });
});

describe("working-doc captions patch", () => {
  it("is null when the toggle already matches the working doc", () => {
    expect(workingCaptionsPatch(project({ captions: { enabled: true } }), true)).toBeNull();
    expect(workingCaptionsPatch(project(), false)).toBeNull(); // undefined captions ⇒ enabled defaults false
  });

  it("patches captions.enabled when the toggle disagrees", () => {
    expect(workingCaptionsPatch(project(), true)).toEqual({ enabled: true });
    expect(workingCaptionsPatch(project({ captions: { enabled: true } }), false)).toEqual({ enabled: false });
  });
});
