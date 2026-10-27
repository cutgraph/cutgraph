// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { TranscriptWord } from "../src/api";
import type { Clip, Project } from "../src/types/project";

const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ w: `w${i}`, start: i + 0.1, end: i + 0.6, segIdx: 0 }));
vi.mock("../src/api", () => ({
  api: {
    transcription: async () => ({ state: "ready", job: null }),
    getTranscript: async () => ({ words: WORDS, segments: [], fillers: [] }),
    getPeaks: () => new Promise(() => {}),
  },
}));

import { audioKind, audioLaneKind } from "../src/timeline/audioKind";
import TimelineLens from "../src/v3/story/lenses/TimelineLens";
import { cuesByWord } from "../src/v3/story/script/script";
import { findCue } from "../src/v3/story/drawer/insert";
import { wordCues } from "../src/v3/story/source/cues";
import { scriptMdx } from "../src/v3/story/source/mdx";
import { cutWordSet, sceneSpans } from "../src/v3/story/model";
import { resetStory } from "../src/v3/story/storyStore";
import { useProjectStore } from "../src/store/useProjectStore";

const MEDIA = "data/source/talk.mp4";
const at = (wordIdx: number) => ({ mode: "word" as const, ref: { media: MEDIA, wordIdx }, offsetF: 0 });
const audio = (id: string, wordIdx: number, extra: Partial<Clip> = {}): Clip => ({
  id, media: { source: `data/audio/${id}.wav` }, srcInF: 0, srcOutF: 30, tlStartF: wordIdx * 30 + 3, anchor: at(wordIdx), ...extra,
});

// Music is ducked under speech; a sound effect is not. The track ids say nothing either way.
const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "kinds", revision: 1, schemaVersion: 3,
  tracks: [
    { id: "speech", kind: "speech", clips: [{ id: "src", media: { source: MEDIA }, srcInF: 0, srcOutF: 210, tlStartF: 0 }] },
    { id: "audio", kind: "audio", clips: [audio("bed", 0, { srcOutF: 150, duckDb: -12 })] },
    { id: "audio_2", kind: "audio", clips: [audio("ding", 2), audio("pop", 4)] },
    { id: "music", kind: "audio", clips: [audio("sting", 1), audio("tune", 5, { duckDb: -6 })] },
  ],
  scenes: [{ id: "s", title: "All", role: "Hook", startWord: 0 }],
};

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} } as unknown as typeof ResizeObserver;
});
afterEach(cleanup);

describe("one music-vs-sound-effect rule", () => {
  it("calls a ducked clip music and an unducked one a sound effect", () => {
    expect(audioKind({ duckDb: -12 })).toBe("music");
    expect(audioKind({ duckDb: 0 })).toBe("music");
    expect(audioKind({})).toBe("sfx");
    expect(audioKind({ duckDb: null })).toBe("sfx");
    expect(PROJECT.tracks.slice(1).map(audioLaneKind)).toEqual(["music", "sfx", "audio"]);
    expect(audioLaneKind({ id: "empty", kind: "audio" })).toBe("audio");
  });

  it("agrees in the script chip, the cue card, the Source view and the Timeline", async () => {
    const chips = cuesByWord(PROJECT, MEDIA);
    expect([0, 1, 2, 4, 5].map((w) => chips.get(w)?.map((c) => c.kind))).toEqual([["music"], ["sfx"], ["sfx"], ["sfx"], ["music"]]);
    expect(["bed", "sting", "ding", "tune"].map((id) => findCue(PROJECT, id)?.kind)).toEqual(["music", "sfx", "sfx", "music"]);
    expect(wordCues(PROJECT, MEDIA, WORDS.length).map((c) => [c.id, c.kind])).toEqual([
      ["bed", "music"], ["sting", "sfx"], ["ding", "sfx"], ["pop", "sfx"], ["tune", "music"],
    ]);
    const mdx = scriptMdx({ project: PROJECT, media: MEDIA, words: WORDS, cut: cutWordSet(PROJECT, MEDIA, WORDS), spans: sceneSpans(PROJECT, MEDIA, WORDS) });
    expect(mdx).toContain('<Music track="bed" /> w0 <Sfx sound="sting" /> w1 <Sfx sound="ding" /> w2');
    expect(mdx).toContain('<Music track="tune" /> w5');

    resetStory();
    useProjectStore.setState({ project: PROJECT, revision: 1, playheadF: 0, selection: null });
    render(<TimelineLens />);
    await screen.findByText("All");
    // Every audio track sits in one Music lane; the clips keep their kind.
    const lanes = [...document.querySelectorAll<HTMLElement>(".tl-row[data-lane=music]")]
      .map((r) => [r.dataset.trackId, r.querySelector(".tl-kind")?.textContent ?? null]);
    expect(lanes).toEqual([["audio", "Music"], ["audio_2", null], ["music", null]]);
    const clipKind = (id: string) => [...document.querySelectorAll<HTMLElement>(".tl-clip")].find((c) => c.textContent?.includes(id))!.className;
    expect(clipKind("ding")).toContain("tl-sfx");
    expect(clipKind("bed")).toContain("tl-music");
  });
});
