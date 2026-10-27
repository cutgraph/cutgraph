// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { TranscriptWord } from "../src/api";
import type { Project } from "../src/types/project";

const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ w: `w${i}`, start: i + 0.1, end: i + 0.6, segIdx: 0 }));
const { saveProject } = vi.hoisted(() => ({ saveProject: vi.fn() }));
vi.mock("../src/api", () => ({
  api: {
    transcription: async () => ({ state: "ready", job: null }),
    getTranscript: async () => ({ words: WORDS, segments: [], fillers: [] }),
    saveProject,
  },
}));
vi.mock("../src/toast", () => ({ toast: () => {} }));

import BoardLens from "../src/v3/story/lenses/BoardLens";
import { moveItem } from "../src/v3/story/board/reorder";
import { resetStory, useStory } from "../src/v3/story/storyStore";
import { undo, useProjectStore } from "../src/store/useProjectStore";

const MEDIA = "data/source/talk.mp4";
const scenes = [
  { id: "s1", title: "Open", role: "Hook" as const, startWord: 0 },
  { id: "s2", title: "Middle", role: "Beat" as const, startWord: 2 },
  { id: "s3", title: "Close", role: "Wrap" as const, startWord: 4 },
];
const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "board-demo", revision: 7, schemaVersion: 3,
  tracks: [
    { id: "speech", kind: "speech", clips: [{ id: "c", media: { source: MEDIA }, srcInF: 0, srcOutF: 210, tlStartF: 0 }] },
    { id: "video", kind: "video", clips: [
      { id: "b1", media: { source: "data/broll/city.mp4" }, srcInF: 0, srcOutF: 30, tlStartF: 93, anchor: { mode: "word", ref: { media: MEDIA, wordIdx: 3 } } },
    ] },
  ],
  scenes,
};
// What the server answers for order s2, s3, s1: the speech re-sequenced and the B-roll moved with word 3.
const NEXT: Project = {
  ...PROJECT,
  tracks: [
    { id: "speech", kind: "speech", clips: [
      { id: "c-s63", media: { source: MEDIA }, srcInF: 63, srcOutF: 210, tlStartF: 0 },
      { id: "c", media: { source: MEDIA }, srcInF: 0, srcOutF: 63, tlStartF: 147 },
    ] },
    { id: "video", kind: "video", clips: [{ ...PROJECT.tracks[1].clips![0], tlStartF: 30 }] },
  ],
  scenes: [scenes[1], scenes[2], scenes[0]],
};

const fetchMock = vi.fn();
const dataTransfer = () => ({ setData: () => {}, getData: () => "", effectAllowed: "", dropEffect: "" });

beforeEach(() => {
  resetStory();
  saveProject.mockReset();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  useProjectStore.setState({ project: PROJECT, revision: 7, playheadF: 0, lastWarnings: [] });
  useProjectStore.temporal.getState().clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function panels(): Promise<HTMLElement[]> {
  await screen.findByText("w0 w1");
  return Array.from(document.querySelectorAll<HTMLElement>(".panel"));
}

describe("Board lens", () => {
  it("moves one item in a list", () => {
    expect(moveItem(["a", "b", "c"], 0, 2)).toEqual(["b", "c", "a"]);
    expect(moveItem(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
  });

  it("shows each scene's role, title, duration, kept words and cue icons", async () => {
    render(<BoardLens />);
    const [open, middle] = await panels();
    expect(open.querySelector(".role")!.textContent).toBe("Hook");
    expect(open.querySelector("b")!.textContent).toBe("Open");
    expect(open.querySelector(".dur")!.textContent).toBe("0:02");
    expect(middle.querySelector("p")!.textContent).toBe("w2 w3");
    expect(middle.querySelector(".cue-broll")!.getAttribute("title")).toBe("city.mp4");
    expect(open.querySelector("video")!.getAttribute("src")).toBe("/media/source/talk#t=0.00");
  });

  it("stills a scene at its first B-roll or graphic", async () => {
    const at = (wordIdx: number) => ({ mode: "word" as const, ref: { media: MEDIA, wordIdx } });
    useProjectStore.setState({
      project: {
        ...PROJECT,
        tracks: [
          ...PROJECT.tracks,
          { id: "stills", kind: "video", clips: [
            { id: "b2", media: { source: "workspaces/garden/library/0123456789abcdef.png" }, srcInF: 0, srcOutF: 45, tlStartF: 153, anchor: at(5) },
          ] },
          { id: "text", kind: "text", items: [
            { type: "graphic", id: "g1", componentId: "lower-third", componentVersion: 1, props: { eyebrow: "", title: "Ana", detail: "" }, pos: { x: 0.1, y: 0.8 }, scale: 1, tlStartF: 30, durF: 60, anchor: at(1) },
          ] },
        ] as Project["tracks"],
      },
    });
    render(<BoardLens />);
    const [open, middle, close] = await panels();
    // Open: its lower third over the footage 55% of the way through the graphic (frame 30 + 33).
    expect(open.querySelector("video.still")!.getAttribute("src")).toBe("/media/source/talk#t=2.10");
    expect(open.querySelector("svg.still-gfx")).not.toBeNull();
    // Middle: its B-roll video, 55% in; Close: its B-roll image.
    expect(middle.querySelector("video.still.broll")!.getAttribute("src")).toBe("/media/source/city#t=0.53");
    expect(close.querySelector("img.still.broll")!.getAttribute("src")).toBe("/media/workspace/garden/0123456789abcdef.png");
    expect(close.querySelector("svg.still-gfx")).toBeNull();
  });

  it("drag to reorder calls the verb, saves at the captured revision and applies one undo step", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ project: NEXT, warnings: [] }) });
    saveProject.mockResolvedValue({ ok: true, project: NEXT, revision: 8 });
    render(<BoardLens />);
    const [first, , last] = await panels();
    fireEvent.dragStart(first, { dataTransfer: dataTransfer() });
    fireEvent.dragOver(last, { dataTransfer: dataTransfer() });
    expect(last.className).toContain("drop");
    fireEvent.drop(last, { dataTransfer: dataTransfer() });

    await waitFor(() => expect(useProjectStore.getState().project).toBe(NEXT));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/verbs/reorder_scenes/board-demo");
    expect(JSON.parse(init.body)).toEqual({ project: PROJECT, order: ["s2", "s3", "s1"], baseRevision: 7 });
    expect(saveProject).toHaveBeenCalledWith("board-demo", NEXT, 7);
    expect(useProjectStore.getState().revision).toBe(8);
    await waitFor(() => expect(Array.from(document.querySelectorAll(".panel b")).map((b) => b.textContent)).toEqual(["Middle", "Close", "Open"]));

    undo();
    expect(useProjectStore.getState().project).toEqual(PROJECT);
  });

  it("leaves the document alone when the server refuses a stale revision", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 409, statusText: "Conflict", json: async () => ({ currentRevision: 9 }) });
    render(<BoardLens />);
    const [first] = await panels();
    fireEvent.keyDown(first, { key: "ArrowRight", altKey: true });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).order).toEqual(["s2", "s1", "s3"]);
    await new Promise((r) => setTimeout(r, 0));
    expect(saveProject).not.toHaveBeenCalled();
    expect(useProjectStore.getState().project).toBe(PROJECT);
    expect(useProjectStore.temporal.getState().pastStates).toHaveLength(0);
  });

  it("selects on click and opens the scene in the script on double-click", async () => {
    render(<BoardLens />);
    const [, middle] = await panels();
    fireEvent.click(middle);
    expect(useStory.getState().caretWord).toBe(2);
    expect(useProjectStore.getState().playheadF).toBe(63);
    await waitFor(() => expect(middle.className).toContain("on"));
    fireEvent.doubleClick(document.querySelectorAll<HTMLElement>(".panel")[2]);
    expect(useStory.getState()).toMatchObject({ lens: "script", caretWord: 4 });
  });
});
