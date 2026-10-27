import { describe, expect, it } from "vitest";
import type { TranscriptWord } from "../src/api";
import type { Project } from "../src/types/project";
import { cutWordSet, sceneSpans } from "../src/v3/story/model";
import { highlightMdx } from "../src/v3/story/source/highlight";
import { scriptMdx } from "../src/v3/story/source/mdx";

const MEDIA = "data/source/talk.mp4";
const TEXT = "So um we open here. Today we build a small thing together. Bye now.";
// One word per second: word i spans [i+0.1, i+0.6) → source frames 3+30i .. 18+30i.
const WORDS: TranscriptWord[] = TEXT.split(" ").map((w, i) => ({ w, start: i + 0.1, end: i + 0.6, segIdx: 0 }));
const word = (idx: number) => ({ mode: "word" as const, ref: { media: MEDIA, wordIdx: idx } });

// Kept: words 0, 2-7 and 10-13. Cut: word 1 ("um") and the run 8-9 ("a small").
const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "demo-talk", revision: 4, schemaVersion: 3,
  tracks: [
    { id: "speech", kind: "speech", clips: [
      { id: "a", media: { source: MEDIA }, srcInF: 0, srcOutF: 33, tlStartF: 0 },
      { id: "b", media: { source: MEDIA }, srcInF: 63, srcOutF: 243, tlStartF: 33 },
      { id: "c", media: { source: MEDIA }, srcInF: 303, srcOutF: 420, tlStartF: 213 },
    ] },
    { id: "video", kind: "video", clips: [
      { id: "city", media: { source: "data/broll/city.mp4" }, srcInF: 0, srcOutF: 60, tlStartF: 150, anchor: word(6) },
      { id: "loose", media: { source: "data/broll/loose.mp4" }, srcInF: 0, srcOutF: 30, tlStartF: 300, anchor: { mode: "time" } },
    ] },
    { id: "music", kind: "audio", clips: [
      { id: "bed", media: { source: "data/music/calm.mp3" }, srcInF: 0, srcOutF: 300, tlStartF: 0, duckDb: -12, anchor: word(0) },
    ] },
    { id: "sfx", kind: "audio", clips: [
      { id: "ding", media: { source: "workspaces/garden/library/5e0123653faf01f9.wav" }, srcInF: 0, srcOutF: 15, tlStartF: 243, anchor: word(12) },
    ] },
    { id: "text", kind: "text", items: [
      { id: "say", text: 'Say "hi"', preset: "lower_third", pos: { x: 0.5, y: 0.8 }, tlStartF: 180, durF: 30, anchor: word(9) },
      { id: "host", type: "graphic", componentId: "lower-third", componentVersion: 1, pos: { x: 0.1, y: 0.8 }, scale: 1,
        props: { eyebrow: "Host", title: "Ana", detail: "Cutgraph" }, tlStartF: 243, durF: 60, anchor: word(12) },
    ] },
  ],
  scenes: [
    { id: "open", title: "Open", role: "Hook", startWord: 0 },
    { id: "build", title: "We build it", role: "Build", startWord: 5, shot: { mode: "animation", pose: "point", expression: "happy", headline: "Build it", cueWord: 7 } },
    { id: "bye", title: "Goodbye", role: "Wrap", startWord: 12 },
  ],
};

const GOLDEN = `---
title: "demo-talk"
kind: footage
source: talk.mp4
words: words.json          # WhisperX timings, one entry per word
format: "16:9"
captions: off
---

## Open {Hook}

<Music track="calm" /> So ~~um~~ we open here.

## We build it {Build}

<Character pose="point" expression="happy" headline="Build it" on="build" />
Today <Broll clip="city" /> we build ~~a~~
<Text preset="lower_third" text="Say &quot;hi&quot;" /> ~~small~~ thing together.

## Goodbye {Wrap}

<Sfx sound="5e0123653faf01f9" />
<Graphic component="lower-third" eyebrow="Host" title="Ana" detail="Cutgraph" /> Bye now.
`;

describe("script.mdx serializer", () => {
  it("projects scenes, cut runs, word-anchored cues and shots (golden)", () => {
    const cut = cutWordSet(PROJECT, MEDIA, WORDS);
    expect([...cut]).toEqual([1, 8, 9]);
    const spans = sceneSpans(PROJECT, MEDIA, WORDS);
    expect(scriptMdx({ project: PROJECT, media: MEDIA, words: WORDS, cut, spans })).toBe(GOLDEN);
  });

  it("names library clips by their imported names and the video by its title", () => {
    const cut = cutWordSet(PROJECT, MEDIA, WORDS);
    const spans = sceneSpans(PROJECT, MEDIA, WORDS);
    const labels = new Map([["workspaces/garden/library/5e0123653faf01f9.wav", "Door Ding.wav"]]);
    const mdx = scriptMdx({ project: { ...PROJECT, title: 'herb care "live"' }, media: MEDIA, words: WORDS, cut, spans, labels });
    expect(mdx.split("\n")[1]).toBe('title: "herb care \\"live\\""');
    expect(mdx).toContain('<Sfx sound="Door Ding" />\n<Graphic');
    expect(mdx).not.toContain("5e0123653faf01f9");
    expect(scriptMdx({ project: { ...PROJECT, title: "  " }, media: MEDIA, words: WORDS, cut, spans })).toMatch(/^---\ntitle: "demo-talk"\n/);
  });

  it("writes a cameo as <Character cameo … />, and no Character for a scene back on footage (its look kept for later)", () => {
    const cut = cutWordSet(PROJECT, MEDIA, WORDS);
    const withShot = (shot: NonNullable<Project["scenes"]>[number]["shot"]) =>
      ({ ...PROJECT, scenes: PROJECT.scenes!.map((s) => (s.id === "build" ? { ...s, shot } : s)) });
    const mdx = (project: Project) => scriptMdx({ project, media: MEDIA, words: WORDS, cut, spans: sceneSpans(project, MEDIA, WORDS) });
    expect(mdx(withShot({ mode: "cameo", expression: "happy", move: "wave", cueWord: 7 })))
      .toContain('## We build it {Build}\n\n<Character cameo expression="happy" move="wave" on="build" />\nToday');
    const footage = mdx(withShot({ pose: "point", expression: "happy", headline: "Build it", cueWord: 7 }));
    expect(footage).toContain("## We build it {Build}\n\nToday");
    expect(footage).not.toContain("<Character");
  });

  it("carries frontmatter: kind, source or narration, words, format and captions", () => {
    const cut = cutWordSet(PROJECT, MEDIA, WORDS);
    const spans = sceneSpans(PROJECT, MEDIA, WORDS);
    const head = (project: Project, media = MEDIA) =>
      scriptMdx({ project, media, words: WORDS, cut, spans }).split("\n---\n")[0].split("\n").slice(2);
    expect(head({ ...PROJECT, width: 1080, height: 1920, captions: { enabled: true } })).toEqual([
      "kind: footage",
      "source: talk.mp4",
      "words: words.json          # WhisperX timings, one entry per word",
      'format: "9:16"',
      "captions: burned-in",
    ]);
    expect(head({ ...PROJECT, narration: "estimated" })).toEqual([
      "kind: animation",
      "narration: none            # estimated timing until you record it",
      "words: words.json          # estimated timings, one entry per word",
      'format: "16:9"',
      "captions: off",
    ]);
    expect(head({ ...PROJECT, narration: "recorded" }, "data/source/explainer_take1.mp4").slice(0, 3)).toEqual([
      "kind: animation",
      "narration: explainer_take1.mp4 # your recording",
      "words: words.json          # WhisperX timings, one entry per word",
    ]);
  });

  it("follows story order and wraps long scenes", () => {
    const moved = { ...PROJECT, scenes: [PROJECT.scenes![2], PROJECT.scenes![0], PROJECT.scenes![1]] };
    const words = Array.from({ length: 40 }, (_, i) => ({ w: `word${i}`, start: i, end: i + 0.5, segIdx: 0 }));
    const mdx = scriptMdx({ project: moved, media: MEDIA, words, cut: new Set(), spans: sceneSpans(moved, MEDIA, words) });
    expect(mdx.match(/^## .*$/gm)).toEqual(["## Goodbye {Wrap}", "## Open {Hook}", "## We build it {Build}"]);
    expect(Math.max(...mdx.split("\n").map((l) => l.length))).toBeLessThanOrEqual(92);
  });
});

describe("script.mdx highlighting", () => {
  it("colours frontmatter, headings, cut runs across lines, strings and tags", () => {
    const segs = highlightMdx('---\ntitle: "x"\n---\n\n## Hi {Hook}\n\n<Broll src="a.mp4" /> ~~cut\nrun~~ kept\n');
    const of = (cls: string) => segs.filter((s) => s.cls === cls).map((s) => s.text);
    expect(of("k")).toEqual(['---\ntitle: "x"\n---\n']);
    expect(of("h")).toEqual(["## Hi "]);
    expect(of("s")).toEqual(["{Hook}", '"a.mp4"']);
    expect(of("t")).toEqual(["Broll"]);
    expect(of("x")).toEqual(["~~cut\nrun~~"]);
    expect(segs.map((s) => s.text).join("")).toBe('---\ntitle: "x"\n---\n\n## Hi {Hook}\n\n<Broll src="a.mp4" /> ~~cut\nrun~~ kept\n');
  });
});
