import { describe, expect, it } from "vitest";
import type { TranscriptWord } from "../src/api";
import type { Clip, Project, Scene } from "../src/types/project";
import { NO_CAST } from "../src/v3/library/cast";
import { cutWordSet, sceneOfWord, sceneRecipe, sceneSpans, speechMedia } from "../src/v3/story/model";

const MEDIA = "talk.mp4";

// Six words, one per second (word i spans [i+0.1, i+0.6)); source runs 0..210f (7s at 30fps).
const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ w: `w${i}`, start: i + 0.1, end: i + 0.6, segIdx: 0 }));

function project(clips: Clip[]): Project {
  return {
    fps: 30, width: 1920, height: 1080, name: "p", revision: 1, schemaVersion: 3,
    tracks: [{ id: "speech", kind: "speech", clips }],
    scenes: [
      { id: "s1", title: "Open", role: "Hook", startWord: 0 },
      { id: "s2", title: "Middle", role: "Beat", startWord: 2 },
      { id: "s3", title: "Close", role: "Wrap", startWord: 4 },
    ],
  };
}

const whole = (): Project => project([{ id: "a", media: { source: MEDIA }, srcInF: 0, srcOutF: 210, tlStartF: 0 }]);

// Word 3 ([3.1s, 3.6s) → frames 93..108) removed: clip a keeps [0,93), clip b keeps [108,210).
const withCut = (): Project =>
  project([
    { id: "a", media: { source: MEDIA }, srcInF: 0, srcOutF: 93, tlStartF: 0 },
    { id: "b", media: { source: MEDIA }, srcInF: 108, srcOutF: 210, tlStartF: 93 },
  ]);

describe("story model", () => {
  it("finds the speech media", () => {
    expect(speechMedia(whole())).toBe(MEDIA);
  });

  it("gives each scene its word range and uncut duration", () => {
    const spans = sceneSpans(whole(), MEDIA, WORDS);
    expect(spans.map((s) => [s.startWord, s.endWord])).toEqual([[0, 2], [2, 4], [4, 6]]);
    expect(spans.map((s) => [s.srcStartF, s.srcEndF])).toEqual([[0, 63], [63, 123], [123, 210]]);
    expect(spans.map((s) => s.durF)).toEqual([63, 60, 87]);
    expect(spans.map((s) => s.tlStartF)).toEqual([0, 63, 123]);
    expect(spans.reduce((a, s) => a + s.durF, 0)).toBe(210);
  });

  it("shrinks a scene by its cut and shifts later scenes", () => {
    const p = withCut();
    expect([...cutWordSet(p, MEDIA, WORDS)]).toEqual([3]);
    const spans = sceneSpans(p, MEDIA, WORDS);
    expect(spans.map((s) => s.durF)).toEqual([63, 45, 87]);
    expect(spans.map((s) => s.tlStartF)).toEqual([0, 63, 108]);
  });

  it("keeps story order when scenes are listed out of word order", () => {
    const p = whole();
    p.scenes = [p.scenes![0], p.scenes![2], p.scenes![1]];
    const spans = sceneSpans(p, MEDIA, WORDS);
    expect(spans.map((s) => s.scene.id)).toEqual(["s1", "s3", "s2"]);
    expect(spans.map((s) => [s.startWord, s.endWord])).toEqual([[0, 2], [4, 6], [2, 4]]);
  });

  it("maps a word to its scene", () => {
    const spans = sceneSpans(whole(), MEDIA, WORDS);
    expect(sceneOfWord(spans, 3)?.scene.id).toBe("s2");
    expect(sceneOfWord(spans, 9)).toBeNull();
  });

  it("is empty without scenes or words", () => {
    const p = whole();
    p.scenes = [];
    expect(sceneSpans(p, MEDIA, WORDS)).toEqual([]);
    expect(sceneSpans(whole(), MEDIA, [])).toEqual([]);
  });
});

describe("sceneRecipe", () => {
  const scene = (shot?: Scene["shot"]): Scene => ({ id: "s1", title: "The plan", role: "Hook", startWord: 0, shot });

  it("is null for a footage scene", () => {
    expect(sceneRecipe(scene(), NO_CAST)).toBeNull();
    expect(sceneRecipe(scene({ mode: "footage" }), NO_CAST)).toBeNull();
  });

  it("names the actor, pose, expression, move and headline of an animated scene", () => {
    const shot = { mode: "animation", pose: "point", expression: "happy", move: "wave", headline: "We shipped it" } as const;
    expect(sceneRecipe(scene(shot), NO_CAST)).toEqual({
      actor: "Lens", pose: "Point", expression: "Happy", move: "Wave", headline: "We shipped it",
    });
  });

  it("also names a cameo scene's shot", () => {
    expect(sceneRecipe(scene({ mode: "cameo", pose: "wave" }), NO_CAST)?.pose).toBe("Wave");
  });
});
