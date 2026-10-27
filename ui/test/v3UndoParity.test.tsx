// @vitest-environment jsdom
import { forwardRef, useImperativeHandle } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TranscriptDoc, TranscriptWord } from "../src/api";
import type { Asset, WorkspaceDetail } from "../src/v3/api";
import type { Clip, Project } from "../src/types/project";

const MEDIA = "talk.mp4";
// Six words, one per second (word i spans [i+0.1, i+0.6)); source runs 0..210f (7s at 30fps).
const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ w: `w${i}`, start: i + 0.1, end: i + 0.6, segIdx: 0 }));

const { getProject, rippleCut, rippleRestore, saveProject } = vi.hoisted(() => ({
  getProject: vi.fn(),
  rippleCut: vi.fn(),
  rippleRestore: vi.fn(),
  saveProject: vi.fn(),
}));
vi.mock("../src/api", () => ({
  api: {
    getProject, rippleCut, rippleRestore, saveProject,
    thumb: (slug: string) => `/media/thumb/${slug}`,
  },
}));
vi.mock("../src/transcript/useTranscript", () => ({
  useTranscript: () => ({
    doc: { language: "en", duration: 7, words: WORDS, segments: [], fillers: [] } as TranscriptDoc,
    lifecycle: { state: "ready", job: null }, loading: false, error: null, refresh: () => {},
  }),
}));
vi.mock("../src/v3/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/v3/api")>();
  return {
    ...actual,
    v3api: {
      ...actual.v3api,
      workspace: (id: string): Promise<WorkspaceDetail> => Promise.resolve({
        workspace: { id, name: "garden", createdAt: "", updatedAt: "", videoCount: 1, assetCount: 0 },
        videos: [],
        assets: id === "garden" ? [plate, seedling] : [],
      }),
      assetThumbUrl: (ws: string, id: string) => `/api/workspaces/${ws}/assets/${id}/thumb`,
    },
  };
});
vi.mock("../src/sse", () => ({ useProjectSSE: () => {} }));
vi.mock("../src/player", () => ({
  FramePlayer: forwardRef((_props, ref) => {
    useImperativeHandle(ref, () => ({
      isSoundBlocked: () => false,
      enableSound: () => {},
      readFrame: () => 0, seek: () => {}, listen: () => {}, unlisten: () => {},
      isRunning: () => false, play: () => {}, pause: () => {},
    }));
    return <div data-testid="mock-player" />;
  }),
}));
vi.mock("../src/preview/ProjectComposition", () => ({ ProjectComposition: () => null }));

const asset = (id: string, name: string, kind: Asset["kind"]): Asset =>
  ({ id: id.repeat(16), sha256: "", name, kind, file: `${id.repeat(16)}${name.slice(name.lastIndexOf("."))}`, bytes: 1, tags: [], createdAt: "" });
const plate = asset("1", "plate.png", "image");
const seedling = asset("2", "seedling.png", "image");

import StoryView from "../src/v3/story/StoryView";
import ScriptLens from "../src/v3/story/lenses/ScriptLens";
import Drawer from "../src/v3/story/Drawer";
import ToastHost from "../src/v3/Toast";
import { commitEdit } from "../src/v3/story/history";
import { openShot, resetStory, setCaret, setRange } from "../src/v3/story/storyStore";
import { adoptSaved, useProjectStore } from "../src/store/useProjectStore";

const clip = (id: string, srcInF: number, srcOutF: number, tlStartF: number): Clip => ({ id, media: { source: MEDIA }, srcInF, srcOutF, tlStartF });
function project(clips: Clip[]): Project {
  return {
    fps: 30, width: 1920, height: 1080, name: "talk", revision: 1, schemaVersion: 3, workspaceId: "garden",
    tracks: [{ id: "speech", kind: "speech", clips }],
    scenes: [
      { id: "s1", title: "Open", role: "Hook", startWord: 0 },
      { id: "s2", title: "Middle", role: "Beat", startWord: 2 },
      { id: "s3", title: "Close", role: "Wrap", startWord: 4 },
    ],
  };
}
const whole = (): Project => project([clip("a", 0, 210, 0)]);
// Word 3 ([3.1s, 3.6s) → frames 93..108) removed.
const withCut = (): Project => project([clip("a", 0, 93, 0), clip("b", 108, 210, 93)]);

function Story() {
  return (
    <div className="v3">
      <section className="story">
        <div className="pane" id="story-pane"><ScriptLens /></div>
        <aside className="side"><Drawer /></aside>
      </section>
      <ToastHost />
    </div>
  );
}

const word = (i: number): HTMLElement => document.querySelector(`[data-word-idx="${i}"]`) as HTMLElement;
const struck = (): number[] => [...document.querySelectorAll<HTMLElement>(".w.cut[data-word-idx]")].map((e) => Number(e.dataset.wordIdx));
const toastEl = (): HTMLElement => screen.getByRole("status");
const history = () => useProjectStore.temporal.getState().pastStates.length;

beforeEach(() => {
  for (const m of [getProject, rippleCut, rippleRestore, saveProject]) m.mockReset();
  saveProject.mockImplementation((_p: string, doc: Project, base: number) => Promise.resolve({ ok: true, project: doc, revision: base + 1 }));
  resetStory();
  useProjectStore.setState({ project: null, revision: 0, playheadF: 0 });
  useProjectStore.temporal.getState().clear();
});
afterEach(cleanup);

describe("Story keyboard history", () => {
  it("Ctrl+Z undoes, Ctrl+Y and Ctrl+Shift+Z redo, and each step is saved", async () => {
    getProject.mockResolvedValue({ project: whole(), revision: 3 });
    render(<StoryView video="talk" />);
    await screen.findByRole("heading", { name: "talk" });
    act(() => commitEdit({ ...whole(), title: "herb care" }, "Renamed video"));
    await waitFor(() => expect(saveProject).toHaveBeenCalledTimes(1));
    const title = () => useProjectStore.getState().project!.title;

    act(() => { fireEvent.keyDown(window, { key: "z", ctrlKey: true }); });
    expect(title()).toBeUndefined();
    await waitFor(() => expect(saveProject).toHaveBeenCalledTimes(2));
    act(() => { fireEvent.keyDown(window, { key: "y", ctrlKey: true }); });
    expect(title()).toBe("herb care");
    await waitFor(() => expect(saveProject).toHaveBeenCalledTimes(3));
    act(() => { fireEvent.keyDown(window, { key: "z", ctrlKey: true }); });
    act(() => { fireEvent.keyDown(window, { key: "Z", ctrlKey: true, shiftKey: true }); });
    expect(title()).toBe("herb care");
    await waitFor(() => expect(saveProject.mock.lastCall![1]).toMatchObject({ title: "herb care" }));
  });
});

describe("Undo on every Story edit toast", () => {
  it("a cut's toast offers Undo, which is exactly one undo step, saved", async () => {
    rippleCut.mockResolvedValue({ project: withCut(), warnings: [] });
    adoptSaved(whole(), 5);
    render(<Story />);
    act(() => setRange({ a: 3, b: 3 }));
    fireEvent.click(screen.getByRole("button", { name: "✂ Cut" }));
    await waitFor(() => expect(struck()).toEqual([3]));
    expect(toastEl().textContent).toContain("Cut 1 word");
    expect(history()).toBe(1);
    const saves = saveProject.mock.calls.length;
    fireEvent.click(within(toastEl()).getByRole("button", { name: "Undo" }));
    expect(struck()).toEqual([]);
    expect(history()).toBe(0);
    await waitFor(() => expect(saveProject.mock.calls.length).toBe(saves + 1));
  });

  it("adding, swapping and removing a cue each toast with Undo", async () => {
    adoptSaved(whole(), 5);
    render(<Story />);
    await screen.findByText("plate.png");
    const videoClips = () => useProjectStore.getState().project!.tracks.filter((t) => t.kind === "video").flatMap((t) => t.clips ?? []);
    const undoToast = () => fireEvent.click(within(toastEl()).getByRole("button", { name: "Undo" }));

    act(() => setCaret(1));
    fireEvent.click(screen.getByText("plate.png").closest("button")!);
    expect(toastEl().textContent).toBe("Added b-roll: plate.pngUndo");
    undoToast();
    expect(videoClips()).toEqual([]);

    fireEvent.click(screen.getByText("plate.png").closest("button")!);
    const card = document.querySelector(".cue-card") as HTMLElement;
    fireEvent.click(within(card).getByText("seedling.png").closest("button")!);
    expect(toastEl().textContent).toBe("Swapped for seedling.pngUndo");
    undoToast();
    expect(videoClips()[0].media.source).toContain(plate.file);

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(videoClips()).toEqual([]);
    expect(toastEl().textContent).toBe("Removed b-rollUndo");
    undoToast();
    expect(videoClips()).toHaveLength(1);
  });

  it("renaming a scene toasts with Undo", () => {
    adoptSaved(whole(), 5);
    render(<Story />);
    const title = screen.getAllByLabelText("Scene title")[1];
    title.textContent = "The middle bit";
    fireEvent.blur(title);
    expect(toastEl().textContent).toBe("Renamed sceneUndo");
    fireEvent.click(within(toastEl()).getByRole("button", { name: "Undo" }));
    expect(useProjectStore.getState().project!.scenes![1].title).toBe("Middle");
  });
});

describe("Backspace and Delete with only a caret", () => {
  it("cuts the caret word, and restores it when pressed on a cut word", async () => {
    rippleCut.mockResolvedValue({ project: withCut(), warnings: [] });
    rippleRestore.mockResolvedValue({ project: whole(), warnings: [] });
    adoptSaved(whole(), 5);
    render(<Story />);
    fireEvent.mouseDown(word(3), { button: 0 });
    fireEvent.mouseUp(window);
    fireEvent.keyDown(window, { key: "Backspace" });
    await waitFor(() => expect(struck()).toEqual([3]));
    expect(rippleCut).toHaveBeenCalledWith("talk", expect.anything(), [{ startF: 93, endF: 108 }]);
    expect(toastEl().textContent).toBe("Cut 1 wordUndo");

    fireEvent.keyDown(window, { key: "Delete" });
    await waitFor(() => expect(struck()).toEqual([]));
    expect(rippleRestore).toHaveBeenCalledWith("talk", expect.anything(), [{ media: MEDIA, srcStartF: 93, srcEndF: 108 }]);
  });

  it("does nothing with no caret, while typing, or under the Shot sheet", () => {
    adoptSaved(whole(), 5);
    render(<Story />);
    fireEvent.keyDown(window, { key: "Backspace" });
    act(() => setCaret(3));
    const input = document.createElement("input");
    document.body.append(input);
    fireEvent.keyDown(input, { key: "Backspace" });
    input.remove();
    act(() => openShot("s2"));
    fireEvent.keyDown(window, { key: "Backspace" });
    expect(rippleCut).not.toHaveBeenCalled();
  });
});
