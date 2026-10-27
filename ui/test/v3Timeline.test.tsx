// @vitest-environment jsdom
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { TranscriptWord } from "../src/api";
import type { Project } from "../src/types/project";

const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ w: `w${i}`, start: i + 0.1, end: i + 0.6, segIdx: 0 }));
vi.mock("../src/api", () => ({
  api: {
    transcription: async () => ({ state: "ready", job: null }),
    getTranscript: async () => ({ words: WORDS, segments: [], fillers: [] }),
    getPeaks: () => new Promise(() => {}),
  },
}));

vi.mock("../src/v3/api", () => ({
  v3api: {
    workspace: async (id: string) => ({
      workspace: { id, name: id, createdAt: "", updatedAt: "", videoCount: 1, assetCount: 1 },
      videos: [],
      assets: id === "garden" ? [{ id: "a1", sha256: "", name: "calm.mp3", kind: "audio", file: "0123456789abcdef.mp3", bytes: 1, tags: [], createdAt: "" }] : [],
    }),
    cast: () => new Promise(() => {}),
  },
}));

import Timeline from "../src/v3/story/lenses/Timeline";
import TimelineLens from "../src/v3/story/lenses/TimelineLens";
import { resetStory, selectCue, useStory } from "../src/v3/story/storyStore";
import { useProjectStore } from "../src/store/useProjectStore";

const MEDIA = "data/source/talk.mp4";
// Story order Close, Open, Middle, already re-sequenced on the timeline: Close 0-87, Open 87-150, Middle 150-210.
const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "tl-demo", revision: 1, schemaVersion: 3,
  tracks: [
    { id: "speech", kind: "speech", clips: [
      { id: "c-s123", media: { source: MEDIA }, srcInF: 123, srcOutF: 210, tlStartF: 0 },
      { id: "c", media: { source: MEDIA }, srcInF: 0, srcOutF: 63, tlStartF: 87 },
      { id: "c-s63", media: { source: MEDIA }, srcInF: 63, srcOutF: 123, tlStartF: 150 },
    ] },
    { id: "video", kind: "video", clips: [
      { id: "b1", media: { source: "data/broll/city.mp4" }, srcInF: 0, srcOutF: 30, tlStartF: 180, anchor: { mode: "word", ref: { media: MEDIA, wordIdx: 3 } } },
    ] },
  ],
  scenes: [
    { id: "s3", title: "Close", role: "Wrap", startWord: 4 },
    { id: "s1", title: "Open", role: "Hook", startWord: 0 },
    { id: "s2", title: "Middle", role: "Beat", startWord: 2 },
  ],
};

// Every lane busy: a lower third and a title in Graphics, a music bed, Middle animated and Close a
// cameo springing in on w5, and captions on.
const LANES_PROJECT: Project = {
  ...PROJECT,
  workspaceId: "garden",
  captions: { enabled: true },
  tracks: [
    ...PROJECT.tracks,
    { id: "text", kind: "text", items: [
      { type: "graphic", id: "g1", componentId: "lower-third", componentVersion: 1, props: { eyebrow: "", title: "Ana", detail: "" }, pos: { x: 0.1, y: 0.8 }, scale: 1, tlStartF: 10, durF: 60 },
      { id: "t1", text: "Hello", preset: "title", pos: { x: 0.5, y: 0.5 }, tlStartF: 100, durF: 30 },
    ] },
    { id: "audio", kind: "audio", clips: [{ id: "m1", media: { source: "workspaces/garden/library/0123456789abcdef.mp3" }, srcInF: 0, srcOutF: 200, tlStartF: 0, duckDb: -12 }] },
  ] as Project["tracks"],
  scenes: [
    { id: "s3", title: "Close", role: "Wrap", startWord: 4, shot: { mode: "cameo", cueWord: 5 } },
    { id: "s1", title: "Open", role: "Hook", startWord: 0 },
    { id: "s2", title: "Middle", role: "Beat", startWord: 2, shot: { mode: "animation", expression: "happy" } },
  ],
};

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} } as unknown as typeof ResizeObserver;
});
beforeEach(() => {
  resetStory();
  useProjectStore.setState({ project: PROJECT, revision: 1, playheadF: 0, selection: null });
});
afterEach(cleanup);

describe("Timeline lens", () => {
  it("draws the scenes as bands above the tracks, in timeline position", async () => {
    render(<TimelineLens />);
    await screen.findByText("Close");
    const bands = Array.from(document.querySelectorAll<HTMLElement>(".tl-band"));
    expect(bands.map((b) => b.textContent)).toEqual(["Close", "Open", "Middle"]);
    // Unmeasured in jsdom, so the timeline uses its 0.5 px/frame default after the lanes' 88px gutter.
    expect(bands.map((b) => [b.style.left, b.style.width])).toEqual([["88px", "43.5px"], ["131.5px", "31.5px"], ["163px", "30px"]]);
    expect(bands[1].style.getPropertyValue("--c")).toBe("var(--yellow)");
    expect(document.querySelector(".timeline")!.className).toBe("timeline v3-timeline");
    const bandRow = document.querySelector(".tl-bands")!;
    expect(bandRow.compareDocumentPosition(document.querySelector(".tl-row")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(bands[2]);
    expect(useProjectStore.getState().playheadF).toBe(150);
    expect(useStory.getState().caretWord).toBe(2);
  });

  it("shares the selected cue with the rest of the Story view", async () => {
    selectCue("b1");
    render(<TimelineLens />);
    await screen.findByText("Close");
    expect(useProjectStore.getState().selection).toBe("b1");
    fireEvent.click(document.querySelector(".tl-row[data-track-id='speech']")!);
    expect(useStory.getState().cueId).toBeNull();
    fireEvent.click(document.querySelector<HTMLElement>(".tl-video")!);
    expect(useStory.getState().cueId).toBe("b1");
  });

  it("names lanes, with library names, derived blocks and the caption pages", async () => {
    useProjectStore.setState({ project: LANES_PROJECT });
    render(<TimelineLens />);
    await screen.findByText("calm");
    const text = (sel: string) => Array.from(document.querySelectorAll<HTMLElement>(sel)).map((e) => e.textContent);
    expect(text(".tl-kind")).toEqual(["Scenes", "Picture", "B-roll", "Graphics", "Music", "Captions"]);
    expect(document.querySelector(".tl-toolbar")).toBeNull();
    expect(document.querySelector(".tl-tid")).toBeNull();
    expect(document.querySelector(".pin")).toBeNull();
    expect(document.querySelector(".tl-wave")).toBeNull();
    expect(document.querySelector(".timeline")!.textContent).not.toMatch(/layer|c-s123|b1/);

    const lane = (name: string) => document.querySelector<HTMLElement>(`.tl-row[data-lane=${name}]`)!;
    const labels = (name: string, sel = ".tl-clip") => Array.from(lane(name).querySelectorAll(sel)).map((e) => e.textContent);
    expect(labels("picture", ".tl-speech")).toEqual(["talk", "talk", "talk"]);
    expect(labels("picture", ".tl-anim")).toEqual(["Lens · Happy"]);
    expect(labels("broll")).toEqual(["city"]);
    expect(labels("graphics", ".tl-graphic, .tl-text")).toEqual(["Lower Third", "Hello"]);
    expect(labels("graphics", ".tl-cameo")).toEqual(["Lens cameo"]);
    expect(labels("music")).toEqual(["calm"]);
    // Close (words 4-5) plays first, then Open (0-1), then Middle (2-3): one page per scene.
    expect(labels("captions")).toEqual(["w4 w5", "w0 w1", "w2 w3"]);
    expect(lane("captions").className).not.toContain("off");

    // The animated scene covers its footage in Picture; its block and a caption page seek.
    const anim = lane("picture").querySelector<HTMLElement>(".tl-anim")!;
    expect([anim.style.left, anim.style.width]).toEqual(["163px", "30px"]);
    fireEvent.click(anim);
    expect(useProjectStore.getState().playheadF).toBe(150);
    fireEvent.click(lane("captions").querySelectorAll<HTMLElement>(".tl-caption")[1]);
    expect(useProjectStore.getState().playheadF).toBe(90);
  });

  it("dims the caption pages while captions are off, and keeps every lane when a kind has no track", async () => {
    useProjectStore.setState({ project: { ...PROJECT, captions: { enabled: false } } });
    render(<TimelineLens />);
    await screen.findByText("Close");
    expect(Array.from(document.querySelectorAll(".tl-row")).map((r) => [(r as HTMLElement).dataset.lane, (r as HTMLElement).dataset.trackId ?? null]))
      .toEqual([["picture", "speech"], ["broll", "video"], ["graphics", null], ["music", null], ["captions", null]]);
    expect(document.querySelector(".tl-captions")!.className).toContain("off");
    expect(document.querySelectorAll(".tl-caption")).toHaveLength(3);
  });

  it("preserves timeline content: tracks, ids, pins, waveforms and toolbar, no bands or lanes", () => {
    render(<Timeline draggedAsset={() => null} onAssetDrop={() => {}} initialZoom={null} onZoomChange={() => {}} />);
    expect(document.querySelector(".timeline")!.className).toBe("timeline");
    expect(document.querySelector(".tl-bands")).toBeNull();
    expect(document.querySelector(".tl-captions")).toBeNull();
    expect(document.querySelector(".tl-toolbar")).not.toBeNull();
    const text = (sel: string) => Array.from(document.querySelectorAll<HTMLElement>(sel)).map((e) => e.textContent);
    expect(text(".tl-kind")).toEqual(["b-roll", "speech"]);
    expect(text(".tl-tid")).toEqual(["video · layer 2", "speech · layer 1"]);
    expect(text(".tl-label")).toEqual(["b1", "c-s123", "c", "c-s63"]);
    expect(document.querySelectorAll(".tl-wave")).toHaveLength(3);
    expect(document.querySelectorAll(".pin")).toHaveLength(1);
    expect(document.querySelector<HTMLElement>(".tl-row")!.style.height).toBe("58px");
  });
});
