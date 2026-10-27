import { describe, expect, it } from "vitest";
import type { Asset } from "../src/v3/api";
import type { TranscriptWord } from "../src/api";
import type { Clip, GraphicItem, Project } from "../src/types/project";
import {
  BROLL_DEFAULT_F,
  BROLL_MIN_S,
  findCue,
  insertAsset,
  insertGraphicAt,
  MUSIC_DUCK_DB,
  swapAsset,
  swapGraphic,
  wordAnchor,
  wordAtFrame,
  wordRunF,
} from "../src/v3/story/drawer/insert";
import type { LibraryAsset } from "../src/v3/story/drawer/library";
import { GRAPHIC_DEFAULT_DURATION_F } from "../src/graphics/ops";
import { resolveMedia, workspaceMediaRef } from "../src/preview/resolveMedia";

const MEDIA = "data/source/talk.mp4";

// Speech keeps source [0,100) at timeline 0 and [150,300) at timeline 100: words 7–9 are cut.
function project(): Project {
  return {
    schemaVersion: 3, name: "talk", fps: 30, width: 1920, height: 1080, revision: 4,
    tracks: [{ id: "speech", kind: "speech", clips: [
      { id: "a", media: { source: MEDIA, proxy: null }, srcInF: 0, srcOutF: 100, tlStartF: 0 },
      { id: "b", media: { source: MEDIA, proxy: null }, srcInF: 150, srcOutF: 300, tlStartF: 100 },
    ] }],
  };
}

// Word i starts at source frame 15·i.
const words: TranscriptWord[] = Array.from({ length: 20 }, (_, i) => ({ w: `w${i}`, start: i * 0.5, end: i * 0.5 + 0.4, segIdx: 0 }));

function entry(kind: Asset["kind"], extra: Partial<Asset> = {}): LibraryAsset {
  const id = { image: "1", video: "2", audio: "3" }[kind].repeat(16);
  const ext = kind === "image" ? ".png" : kind === "video" ? ".mp4" : ".wav";
  return {
    workspaceId: "garden-1a2b3c", workspaceName: "garden",
    asset: { id, sha256: id, name: `${kind} asset${ext}`, kind, file: `${id}${ext}`, bytes: 1, tags: [], createdAt: "", ...extra },
  };
}

function clipOf(p: Project, id: string): { clip: Clip; trackId: string; kind: string } {
  for (const t of p.tracks) {
    const clip = t.clips?.find((c) => c.id === id);
    if (clip) return { clip, trackId: t.id, kind: t.kind };
  }
  throw new Error(`no clip ${id}`);
}

function inserted(result: ReturnType<typeof insertAsset>): { project: Project; id: string; tlStartF: number } {
  if ("error" in result) throw new Error(result.error);
  return result;
}

describe("drawer inserts: asset → word-anchored item", () => {
  it("anchors on the speech media at the word's timeline frame, through cuts", () => {
    expect(wordAnchor(project(), words, MEDIA, 4)).toEqual({
      anchor: { mode: "word", ref: { media: MEDIA, wordIdx: 4 }, offsetF: 0 }, tlStartF: 60,
    });
    expect(wordAnchor(project(), words, MEDIA, 12)?.tlStartF).toBe(130);
    expect(wordAnchor(project(), words, MEDIA, 8)).toBeNull();
    expect(wordAnchor(project(), words, MEDIA, 99)).toBeNull();
  });

  it("places a still image as a video-track clip of the default B-roll length", () => {
    const result = inserted(insertAsset(project(), words, MEDIA, 4, entry("image")));
    const { clip, kind } = clipOf(result.project, result.id);
    expect(kind).toBe("video");
    expect(result.tlStartF).toBe(60);
    expect(clip).toMatchObject({
      media: { source: `workspaces/garden-1a2b3c/library/${entry("image").asset.file}`, proxy: null },
      srcInF: 0, srcOutF: BROLL_DEFAULT_F, tlStartF: 60,
      anchor: { mode: "word", ref: { media: MEDIA, wordIdx: 4 }, offsetF: 0 },
    });
    expect(clip.duckDb).toBeUndefined();
  });

  it("uses a short video's own length and caps a long one", () => {
    const short = inserted(insertAsset(project(), words, MEDIA, 2, entry("video", { durationS: 2 })));
    expect(clipOf(short.project, short.id).clip.srcOutF).toBe(60);
    const long = inserted(insertAsset(project(), words, MEDIA, 2, entry("video", { durationS: 90 })));
    expect(clipOf(long.project, long.id).clip.srcOutF).toBe(BROLL_DEFAULT_F);
  });

  it("puts music on an audio track with duckDb so it ducks under speech, ending with the speech", () => {
    const result = inserted(insertAsset(project(), words, MEDIA, 10, entry("audio", { durationS: 60 })));
    const { clip, kind } = clipOf(result.project, result.id);
    expect(kind).toBe("audio");
    expect(clip).toMatchObject({ tlStartF: 100, srcInF: 0, srcOutF: 150, duckDb: MUSIC_DUCK_DB });
    expect(clip.anchor).toEqual({ mode: "word", ref: { media: MEDIA, wordIdx: 10 }, offsetF: 0 });
  });

  it("keeps a short sound effect whole and unducked", () => {
    const result = inserted(insertAsset(project(), words, MEDIA, 3, entry("audio", { durationS: 1.5 })));
    const { clip, kind } = clipOf(result.project, result.id);
    expect(kind).toBe("audio");
    expect(clip.srcOutF).toBe(45);
    expect(clip.duckDb).toBeUndefined();
  });

  it("stacks an overlapping clip on a new footage layer under the graphics", () => {
    const withGraphic = inserted(insertGraphicAt(project(), words, MEDIA, 1, "callout")).project;
    const first = inserted(insertAsset(withGraphic, words, MEDIA, 2, entry("image")));
    const second = inserted(insertAsset(first.project, words, MEDIA, 3, entry("image")));
    expect(second.project.tracks.map((t) => t.kind)).toEqual(["speech", "video", "video", "text"]);
    expect(clipOf(second.project, second.id).clip.tlStartF).toBe(45);
    expect(second.id).not.toBe(first.id);
  });

  it("adds a registry graphic on a text track with the word's timing, one layer per overlap", () => {
    const first = inserted(insertGraphicAt(project(), words, MEDIA, 12, "lower-third"));
    const item = first.project.tracks.find((t) => t.kind === "text")!.items![0] as GraphicItem;
    expect(item).toMatchObject({
      type: "graphic", id: first.id, componentId: "lower-third", componentVersion: 1,
      props: { eyebrow: "", title: "Title", detail: "" }, scale: 1, tlStartF: 130,
      durF: Math.min(GRAPHIC_DEFAULT_DURATION_F, 250 - 130),
      anchor: { mode: "word", ref: { media: MEDIA, wordIdx: 12 }, offsetF: 0 },
    });
    expect(item.pos.y).toBeGreaterThan(0.7);
    const second = inserted(insertGraphicAt(first.project, words, MEDIA, 12, "callout"));
    const texts = second.project.tracks.filter((t) => t.kind === "text");
    expect(texts).toHaveLength(2);
    expect(texts[1].items![0]).toMatchObject({ id: second.id, tlStartF: 130 });
  });

  it("refuses to start anything on a cut word", () => {
    expect(insertAsset(project(), words, MEDIA, 8, entry("image"))).toEqual({ error: expect.stringContaining("cut") });
    expect(insertGraphicAt(project(), words, MEDIA, 8, "callout")).toEqual({ error: expect.stringContaining("cut") });
  });

  it("finds the kept word under the playhead for click-to-insert", () => {
    expect(wordAtFrame(project(), words, MEDIA, 95)).toBe(6);
    expect(wordAtFrame(project(), words, MEDIA, 115)).toBe(11);
    expect(wordAtFrame(project(), words, MEDIA, 0)).toBe(0);
    expect(wordAtFrame(project(), [], MEDIA, 0)).toBeNull();
  });
});

describe("drawer inserts over a selection", () => {
  it("B-roll spans the selected words, across a cut, from the first word", () => {
    // Words 2–12: source 30..192 keeps [30,100) and [150,192), i.e. timeline 30..142.
    expect(wordRunF(project(), words, MEDIA, 2, 12, 30)).toBe(112);
    const still = inserted(insertAsset(project(), words, MEDIA, 2, entry("image"), 12));
    expect(clipOf(still.project, still.id).clip).toMatchObject({ tlStartF: 30, srcInF: 0, srcOutF: 112, anchor: { ref: { wordIdx: 2 } } });
  });

  it("runs at least two seconds, and a video never past its own length", () => {
    const short = inserted(insertAsset(project(), words, MEDIA, 2, entry("image"), 3));
    expect(clipOf(short.project, short.id).clip.srcOutF).toBe(BROLL_MIN_S * 30);
    const video = inserted(insertAsset(project(), words, MEDIA, 2, entry("video", { durationS: 3.25 }), 12));
    expect(clipOf(video.project, video.id).clip.srcOutF).toBe(98);
  });

  it("stops at the end of the speech", () => {
    const tail = inserted(insertAsset(project(), words, MEDIA, 18, entry("image"), 19));
    expect(clipOf(tail.project, tail.id).clip).toMatchObject({ tlStartF: 220, srcOutF: 30 });
  });

  it("leaves music and sound effects at their own length", () => {
    const music = inserted(insertAsset(project(), words, MEDIA, 10, entry("audio", { durationS: 60 }), 12));
    expect(clipOf(music.project, music.id).clip).toMatchObject({ tlStartF: 100, srcOutF: 150, duckDb: MUSIC_DUCK_DB });
    const sting = inserted(insertAsset(project(), words, MEDIA, 2, entry("audio", { durationS: 1.5 }), 12));
    expect(clipOf(sting.project, sting.id).clip.srcOutF).toBe(45);
  });
});

describe("drawer cues: find, swap", () => {
  it("reads each inserted kind back as a cue on its word", () => {
    let p = project();
    const ids: Record<string, string> = {};
    for (const [key, e, w] of [["broll", entry("image"), 1], ["music", entry("audio", { durationS: 60 }), 2], ["sfx", entry("audio", { durationS: 1 }), 3]] as const) {
      const r = inserted(insertAsset(p, words, MEDIA, w, e));
      p = r.project;
      ids[key] = r.id;
    }
    const g = inserted(insertGraphicAt(p, words, MEDIA, 5, "chapter-card"));
    expect(findCue(g.project, ids.broll)).toMatchObject({ kind: "broll", wordIdx: 1 });
    expect(findCue(g.project, ids.music)).toMatchObject({ kind: "music", wordIdx: 2 });
    expect(findCue(g.project, ids.sfx)).toMatchObject({ kind: "sfx", wordIdx: 3 });
    expect(findCue(g.project, g.id)).toMatchObject({ kind: "graphic", wordIdx: 5, tlStartF: 75 });
    expect(findCue(g.project, "a")).toBeNull();
  });

  it("swaps media in place, keeping the word, start and never growing the span", () => {
    const base = inserted(insertAsset(project(), words, MEDIA, 4, entry("image")));
    const next = entry("video", { id: "b".repeat(16), file: `${"b".repeat(16)}.mp4`, durationS: 2 });
    const swapped = swapAsset(base.project, base.id, next);
    expect(clipOf(swapped, base.id).clip).toMatchObject({
      media: { source: `workspaces/garden-1a2b3c/library/${"b".repeat(16)}.mp4`, proxy: null },
      srcInF: 0, srcOutF: 60, tlStartF: 60, anchor: { mode: "word", ref: { media: MEDIA, wordIdx: 4 }, offsetF: 0 },
    });
    expect(swapAsset(swapped, base.id, next)).toBe(swapped);
  });

  it("swaps a graphic's template, keeping its timing and anchor", () => {
    const base = inserted(insertGraphicAt(project(), words, MEDIA, 4, "lower-third"));
    const swapped = swapGraphic(base.project, base.id, "callout");
    const item = findCue(swapped, base.id)!.item!;
    expect(item).toMatchObject({ componentId: "callout", tlStartF: 60, anchor: { mode: "word", ref: { media: MEDIA, wordIdx: 4 } } });
    expect(Object.keys(item.props).sort()).toEqual(["accent", "body", "label"]);
    expect(swapGraphic(swapped, base.id, "callout")).toBe(swapped);
  });
});

describe("library media in the preview", () => {
  it("serves a workspace ref from the workspace media route, and nothing else from it", () => {
    const ref = workspaceMediaRef("garden-1a2b3c", `${"a".repeat(16)}.mp4`);
    expect(resolveMedia({ source: ref, proxy: null })).toBe(`/media/workspace/garden-1a2b3c/${"a".repeat(16)}.mp4`);
    expect(resolveMedia({ source: "workspaces/garden/library/../assets.json", proxy: null })).not.toContain("/media/workspace/");
    expect(resolveMedia({ source: "workspaces/garden/library/thumbs/x.jpg", proxy: null })).not.toContain("/media/workspace/");
  });
});
