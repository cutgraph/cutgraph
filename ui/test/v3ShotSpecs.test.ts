import { describe, expect, it } from "vitest";
import { ACCENT, EYEBROW, ROLE_LOOKS } from "motion-core";
import type { TranscriptWord } from "../src/api";
import type { Clip, Project, Scene, Shot } from "../src/types/project";
import { sceneSpans } from "../src/v3/story/model";
import { animatedSceneSpecs, sceneSpec, withShot } from "../src/v3/story/shot/specs";

const MEDIA = "talk.mp4";

// Six words, one per second: word i starts at source frame 30i+3 (7s of source at 30fps).
const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ w: `w${i}`, start: i + 0.1, end: i + 0.6, segIdx: 0 }));
const clip = (id: string, srcInF: number, srcOutF: number, tlStartF: number): Clip => ({ id, media: { source: MEDIA }, srcInF, srcOutF, tlStartF });

function project(clips: Clip[], shots: Record<string, Shot> = {}, order = ["s1", "s2", "s3"]): Project {
  const scenes: Record<string, Scene> = {
    s1: { id: "s1", title: "Open", role: "Hook", startWord: 0 },
    s2: { id: "s2", title: "Middle", role: "Beat", startWord: 2 },
    s3: { id: "s3", title: "Close", role: "Wrap", startWord: 4 },
  };
  return {
    fps: 30, width: 1920, height: 1080, name: "p", revision: 1, schemaVersion: 3,
    tracks: [{ id: "speech", kind: "speech", clips }],
    scenes: order.map((id) => (shots[id] ? { ...scenes[id], shot: shots[id] } : scenes[id])),
  };
}

const whole = [clip("a", 0, 210, 0)];
const specs = (p: Project) => animatedSceneSpecs(p, WORDS, sceneSpans(p, MEDIA, WORDS), MEDIA);

describe("scene render specs", () => {
  it("draws only animated and cameo scenes, each over its span with the role look filled in", () => {
    const p = project(whole, { s1: { mode: "animation", pose: "cheer" }, s2: { pose: "wave" }, s3: { mode: "cameo" } });
    expect(specs(p)).toEqual([
      {
        sceneId: "s1", mode: "animation", startF: 0, durF: 63,
        shot: { character: "lens", palette: "periwinkle", ...ROLE_LOOKS.Hook, pose: "cheer", headline: "Open", eyebrow: EYEBROW, accent: ACCENT },
        timing: { firstT: 0.1, cueT: 0.1, endT: 2.1 },
      },
      {
        sceneId: "s3", mode: "cameo", startF: 123, durF: 87,
        shot: { character: "lens", palette: "periwinkle", ...ROLE_LOOKS.Wrap, headline: "Close", eyebrow: EYEBROW, accent: ACCENT },
        timing: { firstT: 0, cueT: 0, endT: 2.9 },
      },
    ]);
  });

  it("leaves footage scenes out, whether the mode is absent or explicit", () => {
    expect(specs(project(whole))).toEqual([]);
    expect(specs(project(whole, { s2: { mode: "footage", expression: "happy" } }))).toEqual([]);
  });

  it("lands the diagram on the cue word, in scene-local seconds", () => {
    const [spec] = specs(project(whole, { s2: { mode: "animation", cueWord: 3, headline: "Mid" } }));
    expect(spec).toMatchObject({ sceneId: "s2", startF: 63, durF: 60, timing: { firstT: 0, cueT: 1 } });
    expect(spec.shot.headline).toBe("Mid");
  });

  it("ignores a cue word outside the scene", () => {
    const [spec] = specs(project(whole, { s1: { mode: "animation", cueWord: 4 } }));
    expect(spec.timing).toEqual({ firstT: 0.1, cueT: 0.1, endT: 2.1 });
  });

  it("maps through cuts inside a scene: a cut cue word falls back to the first kept word", () => {
    // Word 3 (source 93..108) cut: scene 2 shrinks to 45 frames and scene 3 moves up to 108.
    const cut = [clip("a", 0, 93, 0), clip("b", 108, 210, 93)];
    expect(specs(project(cut, { s2: { mode: "animation", cueWord: 3 }, s3: { mode: "cameo", cueWord: 5 } }))).toMatchObject([
      { sceneId: "s2", startF: 63, durF: 45, timing: { firstT: 0, cueT: 0 } },
      { sceneId: "s3", startF: 108, durF: 87, timing: { firstT: 0, cueT: 1 } },
    ]);
  });

  it("takes the first KEPT word when a scene's opening word is cut", () => {
    // Word 2 (source 63..78) cut: scene 2 starts at the kept source 78, and word 3 (source 93) is 15 frames in.
    const cut = [clip("a", 0, 63, 0), clip("b", 78, 210, 63)];
    const [spec] = specs(project(cut, { s2: { mode: "animation" } }));
    expect(spec).toMatchObject({ startF: 63, durF: 45, timing: { firstT: 0.5, cueT: 0.5 } });
  });

  it("follows a reordered story: specs come in story order at their new timeline frames", () => {
    // Story order s2, s3, s1: source 63..210 plays first, then 0..63.
    const reordered = [clip("c-s63", 63, 210, 0), clip("c", 0, 63, 147)];
    const p = project(reordered, { s1: { mode: "animation" }, s3: { mode: "cameo", cueWord: 5 } }, ["s2", "s3", "s1"]);
    expect(specs(p)).toMatchObject([
      { sceneId: "s3", startF: 60, durF: 87, timing: { firstT: 0, cueT: 1 } },
      { sceneId: "s1", startF: 147, durF: 63, timing: { firstT: 0.1, cueT: 0.1 } },
    ]);
  });

  it("excludes a scene whose words are all cut", () => {
    const cut = [clip("a", 0, 63, 0), clip("b", 123, 210, 63)];
    const p = project(cut, { s2: { mode: "animation" }, s3: { mode: "animation" } });
    expect(specs(p).map((s) => [s.sceneId, s.startF, s.durF])).toEqual([["s3", 63, 87]]);
    expect(sceneSpec(p, WORDS, sceneSpans(p, MEDIA, WORDS)[1], MEDIA)).toBeNull();
  });

  it("gives the Shot view a spec for a footage scene too", () => {
    const p = project(whole);
    expect(sceneSpec(p, WORDS, sceneSpans(p, MEDIA, WORDS)[2], MEDIA)).toMatchObject({ sceneId: "s3", mode: "footage", startF: 123 });
  });

  it("has no specs without words or speech media", () => {
    const p = project(whole, { s1: { mode: "animation" } });
    expect(animatedSceneSpecs(p, [], sceneSpans(p, MEDIA, []), MEDIA)).toEqual([]);
    expect(animatedSceneSpecs(p, WORDS, sceneSpans(p, null, WORDS), null)).toEqual([]);
  });
});

describe("shot patches", () => {
  const base = project(whole, { s1: { mode: "animation", pose: "cheer" } });
  const shotOf = (p: Project, id: string) => p.scenes?.find((s) => s.id === id)?.shot;

  it("sets each picker's field on only that scene", () => {
    const picks: Shot[] = [
      { pose: "wave" }, { expression: "excited" }, { move: "hop" }, { backdrop: "route" }, { diagram: "checks" },
      { headline: "Hello" }, { cueWord: 1 }, { character: "notes-guest" }, { eyebrow: "TEAM NOTES" }, { eyebrow: "" },
    ];
    for (const patch of picks) {
      const next = withShot(base, "s1", patch);
      expect(shotOf(next, "s1")).toEqual({ mode: "animation", pose: "cheer", ...patch });
      expect(next.scenes?.[1]).toBe(base.scenes?.[1]);
    }
  });

  it("starts a shot on a scene that has none", () => {
    expect(shotOf(withShot(base, "s2", { mode: "cameo" }), "s2")).toEqual({ mode: "cameo" });
  });

  it("switches modes, storing footage as no mode and keeping the look for switching back", () => {
    const cameo = withShot(base, "s1", { mode: "cameo" });
    expect(shotOf(cameo, "s1")).toEqual({ mode: "cameo", pose: "cheer" });
    const footage = withShot(cameo, "s1", { mode: "footage" });
    expect(shotOf(footage, "s1")).toEqual({ pose: "cheer" });
    expect(shotOf(withShot(footage, "s1", { mode: "animation" }), "s1")).toEqual({ mode: "animation", pose: "cheer" });
  });

  it("removes a shot left empty, and an empty headline", () => {
    const onlyMode = project(whole, { s1: { mode: "cameo" } });
    expect(withShot(onlyMode, "s1", { mode: "footage" }).scenes?.[0]).not.toHaveProperty("shot");
    const titled = withShot(base, "s1", { headline: "Hi" });
    expect(shotOf(withShot(titled, "s1", { headline: "" }), "s1")).toEqual({ mode: "animation", pose: "cheer" });
  });

  it("keeps an empty eyebrow, which hides it, and clears an own eyebrow back to the brand's with null", () => {
    const hidden = withShot(base, "s1", { eyebrow: "" });
    expect(shotOf(hidden, "s1")).toEqual({ mode: "animation", pose: "cheer", eyebrow: "" });
    expect(withShot(hidden, "s1", { eyebrow: "" })).toBe(hidden);
    expect(shotOf(withShot(hidden, "s1", { eyebrow: null }), "s1")).toEqual({ mode: "animation", pose: "cheer" });
    const [spec] = specs(hidden);
    expect([spec.shot.eyebrow, spec.shot.accent]).toEqual(["", ACCENT]);
  });

  it("returns the same project when nothing changes", () => {
    expect(withShot(base, "s1", { pose: "cheer" })).toBe(base);
    expect(withShot(base, "s1", { mode: "animation" })).toBe(base);
    expect(withShot(base, "s2", { mode: "footage" })).toBe(base);
    expect(withShot(base, "missing", { pose: "wave" })).toBe(base);
  });
});
