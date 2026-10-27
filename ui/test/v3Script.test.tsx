// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TranscriptDoc, TranscriptLifecycle, TranscriptWord } from "../src/api";
import type { Clip, Project } from "../src/types/project";

const { rippleCut, rippleRestore, saveProject, transcript } = vi.hoisted(() => ({
  rippleCut: vi.fn(),
  rippleRestore: vi.fn(),
  saveProject: vi.fn(),
  transcript: { doc: null as TranscriptDoc | null, lifecycle: null as TranscriptLifecycle | null },
}));
vi.mock("../src/api", () => ({ api: { rippleCut, rippleRestore, saveProject } }));
vi.mock("../src/transcript/useTranscript", () => ({
  useTranscript: () => ({ ...transcript, loading: false, error: null, refresh: () => {} }),
}));

import ScriptLens from "../src/v3/story/lenses/ScriptLens";
import { cuesByWord, rangeAllCut, restoreRanges, selectionCuts } from "../src/v3/story/script/script";
import { resetStory, setRange, useStory } from "../src/v3/story/storyStore";
import { adoptSaved, metaState, setPlayhead, useProjectStore } from "../src/store/useProjectStore";
import { cutWordSet } from "../src/v3/story/model";
import { withCameo } from "../src/v3/story/script/cameo";

const MEDIA = "talk.mp4";
// Six words, one per second (word i spans [i+0.1, i+0.6)); source runs 0..210f (7s at 30fps).
const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ w: `w${i}`, start: i + 0.1, end: i + 0.6, segIdx: 0 }));
const clip = (id: string, srcInF: number, srcOutF: number, tlStartF: number): Clip => ({ id, media: { source: MEDIA }, srcInF, srcOutF, tlStartF });

function project(clips: Clip[], extra: Project["tracks"] = []): Project {
  return {
    fps: 30, width: 1920, height: 1080, name: "talk", revision: 1, schemaVersion: 3,
    tracks: [{ id: "speech", kind: "speech", clips }, ...extra],
    scenes: [
      { id: "s1", title: "Open", role: "Hook", startWord: 0 },
      { id: "s2", title: "Middle", role: "Beat", startWord: 2 },
      { id: "s3", title: "Close", role: "Wrap", startWord: 4 },
    ],
  };
}
const whole = (extra: Project["tracks"] = []): Project => project([clip("a", 0, 210, 0)], extra);
// Word 3 ([3.1s, 3.6s) → frames 93..108) removed.
const withCut = (): Project => project([clip("a", 0, 93, 0), clip("b", 108, 210, 93)]);

const doc = (fillers: TranscriptDoc["fillers"] = []): TranscriptDoc => ({ language: "en", duration: 7, words: WORDS, segments: [], fillers });

function load(p: Project, d: TranscriptDoc | null = doc()): ReturnType<typeof render> {
  transcript.doc = d;
  transcript.lifecycle = d ? { state: "ready", job: null } : null;
  adoptSaved(p, 5);
  return render(<ScriptLens />);
}

const wordEl = (c: HTMLElement, i: number): HTMLElement => c.querySelector(`[data-word-idx="${i}"]`) as HTMLElement;
const struck = (c: HTMLElement): number[] =>
  [...c.querySelectorAll<HTMLElement>(".w.cut[data-word-idx]")].map((e) => Number(e.dataset.wordIdx));

function drag(c: HTMLElement, a: number, b: number): void {
  fireEvent.mouseDown(wordEl(c, a), { button: 0 });
  fireEvent.mouseOver(wordEl(c, b));
  fireEvent.mouseUp(window);
}

beforeEach(() => {
  rippleCut.mockReset();
  rippleRestore.mockReset();
  saveProject.mockReset();
  resetStory();
  useProjectStore.setState({ project: null, revision: 0, playheadF: 0 });
  useProjectStore.temporal.getState().clear();
});
afterEach(cleanup);

describe("Script lens sections", () => {
  it("links to Add-ons for a video without a previous job using the lifecycle code", () => {
    transcript.doc = null;
    transcript.lifecycle = { state: "failed", job: null, errorCode: "TRANSCRIPTION_NOT_INSTALLED" };
    adoptSaved(whole(), 1);
    render(<ScriptLens />);
    expect(screen.getByRole("link", { name: "Open Settings, Add-ons" })).toBeTruthy();
  });
  it("links to Add-ons only for the explicit unavailable error code", () => {
    transcript.doc = null;
    transcript.lifecycle = { state: "failed", job: { id: "job", kind: "transcribe", status: "error", progress: 1,
      progressStage: "Failed", canCancel: false, canRetry: true, errorCode: "TRANSCRIPTION_NOT_INSTALLED" } };
    adoptSaved(whole(), 1);
    render(<ScriptLens />);
    expect(screen.getByRole("link", { name: "Open Settings, Add-ons" })).toBeTruthy();
  });
  it.each(["not_started", "failed", "stale"] as const)("keeps the %s transcript state within Story", (state) => {
    transcript.doc = null;
    transcript.lifecycle = { state, job: null };
    adoptSaved(whole(), 1);
    render(<ScriptLens />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(document.querySelector('[data-slot="script"]')).toBeTruthy();
    if (state === "failed") expect(screen.getByText("The transcription could not be completed.")).toBeTruthy();
  });

  it("groups each scene's words into its own section, in story order", () => {
    const { container } = load(whole());
    const sections = [...container.querySelectorAll<HTMLElement>(".scene")];
    expect(sections.map((s) => s.dataset.scene)).toEqual(["s1", "s2", "s3"]);
    expect(sections.map((s) => [...s.querySelectorAll<HTMLElement>("[data-word-idx]")].map((w) => w.textContent))).toEqual([
      ["w0", "w1"], ["w2", "w3"], ["w4", "w5"],
    ]);
    expect(sections.map((s) => s.querySelector(".role")?.textContent)).toEqual(["Hook", "Beat", "Wrap"]);
    expect(sections.map((s) => s.querySelector(".dur")?.textContent)).toEqual(["0:02", "0:02", "0:02"]);
  });

  it("shows one section holding every word when there are no scenes", () => {
    const p = whole();
    p.scenes = [];
    const { container } = load(p);
    expect(container.querySelectorAll(".scene")).toHaveLength(1);
    expect(container.querySelectorAll("[data-word-idx]")).toHaveLength(6);
    expect(screen.getByText("Your script")).toBeTruthy();
  });

  it("shows a friendly state while the transcript is being made", () => {
    transcript.doc = null;
    transcript.lifecycle = { state: "running", job: null };
    adoptSaved(whole(), 1);
    render(<ScriptLens />);
    expect(screen.getByText("Preparing your footage")).toBeTruthy();
    expect(screen.getByText("Transcribing every word with WhisperX")).toBeTruthy();
  });
});

describe("Script lens cuts", () => {
  it("derives struck words from the speech clips", () => {
    expect([...cutWordSet(withCut(), MEDIA, WORDS)]).toEqual([3]);
    const { container } = load(withCut());
    expect(struck(container)).toEqual([3]);
    expect(rangeAllCut(new Set([3]), 3, 3)).toBe(true);
    expect(rangeAllCut(new Set([3]), 2, 3)).toBe(false);
  });

  it("turns a selection into the kept source runs under it", () => {
    expect(selectionCuts(whole(), MEDIA, WORDS, 2, 3)).toEqual([
      { media: MEDIA, srcStartF: 63, srcEndF: 108, kind: "selection", durSec: 1.5 },
    ]);
    // Across the already-cut word 3 the selection splits around it instead of over-cutting.
    expect(selectionCuts(withCut(), MEDIA, WORDS, 2, 4).map((c) => [c.srcStartF, c.srcEndF])).toEqual([[63, 93], [108, 138]]);
    expect(restoreRanges(withCut(), MEDIA, WORDS, 3, 3)).toEqual([{ media: MEDIA, srcStartF: 93, srcEndF: 108 }]);
  });

  it("cuts a dragged selection through the ripple-cut verb as one undo step", async () => {
    rippleCut.mockResolvedValue({ project: { ...withCut(), revision: 5 }, warnings: [] });
    saveProject.mockResolvedValue({ ok: true, revision: 6 });
    const { container } = load(whole());
    drag(container, 3, 3);
    expect(useStory.getState().caretWord).toBe(3);
    act(() => setRange({ a: 3, b: 3 }));
    fireEvent.click(screen.getByRole("button", { name: "✂ Cut" }));
    await waitFor(() => expect(struck(container)).toEqual([3]));
    expect(rippleCut).toHaveBeenCalledWith("talk", expect.anything(), [{ startF: 93, endF: 108 }]);
    expect(metaState().past.at(-1)?.label).toBe("Cut 0.5s");
    expect(useStory.getState().range).toBeNull();
  });

  it("drag-selects within a scene and cuts on Delete", async () => {
    rippleCut.mockResolvedValue({ project: withCut(), warnings: [] });
    saveProject.mockResolvedValue({ ok: true, revision: 6 });
    const { container } = load(whole());
    fireEvent.mouseDown(wordEl(container, 2), { button: 0 });
    fireEvent.mouseOver(wordEl(container, 3));
    fireEvent.mouseOver(wordEl(container, 4)); // another scene: the drag stays in its own
    fireEvent.mouseUp(window);
    expect(useStory.getState().range).toEqual({ a: 2, b: 3 });
    expect([...container.querySelectorAll<HTMLElement>(".w.selw")].map((e) => e.dataset.wordIdx)).toEqual(["2", "3"]);
    fireEvent.keyDown(window, { key: "Delete" });
    await waitFor(() => expect(rippleCut).toHaveBeenCalledWith("talk", expect.anything(), [{ startF: 63, endF: 108 }]));
  });

  it("offers Restore for an all-cut selection and sends it to ripple_restore", async () => {
    rippleRestore.mockResolvedValue({ project: whole(), warnings: [] });
    saveProject.mockResolvedValue({ ok: true, revision: 6 });
    const { container } = load(withCut());
    act(() => setRange({ a: 3, b: 3 }));
    fireEvent.click(screen.getByRole("button", { name: "↺ Restore" }));
    await waitFor(() => expect(struck(container)).toEqual([]));
    expect(rippleRestore).toHaveBeenCalledWith("talk", expect.anything(), [{ media: MEDIA, srcStartF: 93, srcEndF: 108 }]);
    expect(metaState().past.at(-1)?.label).toBe("Restored 1 word");
  });

  it("clears the selection on Escape", () => {
    load(whole());
    act(() => setRange({ a: 0, b: 1 }));
    expect(screen.getByRole("toolbar", { name: "Selection" })).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(useStory.getState().range).toBeNull();
    expect(screen.queryByRole("toolbar", { name: "Selection" })).toBeNull();
  });

  it("opens the drawer from the toolbar's cue buttons", () => {
    load(whole());
    act(() => setRange({ a: 0, b: 1 }));
    fireEvent.click(screen.getByRole("button", { name: "◆ Graphic" }));
    expect(useStory.getState().drawerTab).toBe("graphics");
  });
});

describe("Script lens ✦ Character", () => {
  beforeEach(() => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });

  it("makes the selection's scene a cameo that springs in on its first word, as one undo step, saved", async () => {
    saveProject.mockResolvedValue({ ok: true, revision: 6 });
    load(whole());
    act(() => setRange({ a: 2, b: 3 }));
    fireEvent.click(screen.getByRole("button", { name: "✦ Character" }));
    const pick = screen.getByRole("dialog", { name: "Pick" });
    expect(within(pick).getByText("Add animation")).toBeTruthy();
    const tile = within(pick).getByRole("button", { name: /Lens cameo/ });
    expect(tile.textContent).toContain("Springs in on “w2”");
    fireEvent.click(tile);
    const p = useProjectStore.getState().project!;
    expect(p.scenes!.map((s) => s.shot)).toEqual([undefined, { mode: "cameo", cueWord: 2 }, undefined]);
    expect(metaState().past.at(-1)?.label).toBe("Added Lens cameo on “w2”");
    expect(useProjectStore.temporal.getState().pastStates).toHaveLength(1);
    await waitFor(() => expect(saveProject).toHaveBeenCalledWith("talk", p, 5));
    expect([useStory.getState().caretWord, useProjectStore.getState().playheadF]).toEqual([2, 63]);
    act(() => useProjectStore.temporal.getState().undo());
    expect(useProjectStore.getState().project!.scenes![1].shot).toBeUndefined();
  });

  it("re-anchors a scene's shot on the new word and keeps the rest of its look", () => {
    const p = whole();
    p.scenes![2] = { ...p.scenes![2], shot: { mode: "animation", expression: "excited", cueWord: 4 } };
    load(p);
    act(() => setRange({ a: 5, b: 5 }));
    fireEvent.click(screen.getByRole("button", { name: "✦ Character" }));
    fireEvent.click(screen.getByRole("button", { name: /cameo/ }));
    expect(useProjectStore.getState().project!.scenes![2].shot).toEqual({ mode: "cameo", expression: "excited", cueWord: 5 });
  });

  it("starts on the first kept word, and is not offered on an all-cut selection or a script video", () => {
    expect(withCameo(withCut(), "s2", 2).scenes![1].shot).toEqual({ mode: "cameo", cueWord: 2 });
    load(withCut());
    act(() => setRange({ a: 3, b: 3 }));
    expect(screen.getByRole("toolbar", { name: "Selection" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "✦ Character" })).toBeNull();
    cleanup();
    load({ ...whole(), narration: "estimated" });
    act(() => setRange({ a: 0, b: 1 }));
    expect(screen.queryByRole("button", { name: "✦ Character" })).toBeNull();
  });
});

describe("Script lens seek and playhead", () => {
  it("seeks the playhead to a clicked word and lights the playhead word", () => {
    const { container } = load(withCut());
    fireEvent.mouseDown(wordEl(container, 4), { button: 0 });
    fireEvent.mouseUp(window);
    expect(useProjectStore.getState().playheadF).toBe(123 - 15); // source 123 sits after the 15f cut
    expect(useStory.getState().caretWord).toBe(4);
    expect(wordEl(container, 4).hasAttribute("data-now")).toBe(true);
    act(() => setPlayhead(35));
    expect(wordEl(container, 1).hasAttribute("data-now")).toBe(true);
    expect(container.querySelectorAll("[data-now]")).toHaveLength(1);
  });
});

describe("Script lens cue chips", () => {
  const cued = (): Project =>
    whole([
      { id: "v", kind: "video", clips: [{ id: "b1", media: { source: "media/city-night.mp4" }, srcInF: 0, srcOutF: 60, tlStartF: 63, anchor: { mode: "word", ref: { media: MEDIA, wordIdx: 2 } } }] },
      { id: "t", kind: "text", items: [
        { id: "x1", text: "Hello", preset: "lower-third", pos: { x: 0.5, y: 0.8 }, tlStartF: 125, durF: 30, anchor: { mode: "word", ref: { media: MEDIA, wordIdx: 4 } } },
        { id: "x2", text: "Pinned", preset: "lower-third", pos: { x: 0.5, y: 0.8 }, tlStartF: 10, durF: 30, anchor: { mode: "time" } },
      ] },
    ]);

  it("places word-anchored clips and items before their anchor word", () => {
    const map = cuesByWord(cued(), MEDIA);
    expect([...map.entries()]).toEqual([
      [2, [{ id: "b1", kind: "broll", label: "city night" }]],
      [4, [{ id: "x1", kind: "graphic", label: "Hello" }]],
    ]);
    const { container } = load(cued());
    const chip = container.querySelector<HTMLElement>('[data-cue="b1"]')!;
    expect(chip.closest(".scene")?.getAttribute("data-scene")).toBe("s2");
    expect(chip.nextElementSibling?.getAttribute("data-word-idx")).toBe("2");
    expect(chip.textContent).toBe("▣ city night");
    expect(container.querySelector('[data-cue="x2"]')).toBeNull();
  });

  it("selects a cue when its chip is clicked", () => {
    const { container } = load(cued());
    fireEvent.click(container.querySelector('[data-cue="x1"]')!);
    expect(useStory.getState().cueId).toBe("x1");
    expect(container.querySelector('[data-cue="x1"]')?.classList.contains("on")).toBe(true);
    expect(useProjectStore.getState().playheadF).toBe(125);
  });
});

describe("Script lens scene rename", () => {
  it("renames a scene in the doc as one undo step and saves it", () => {
    saveProject.mockResolvedValue({ ok: true, revision: 6 });
    load(whole());
    const title = screen.getAllByLabelText("Scene title")[1];
    title.textContent = "The middle bit";
    fireEvent.blur(title);
    const p = useProjectStore.getState().project!;
    expect(p.scenes!.map((s) => s.title)).toEqual(["Open", "The middle bit", "Close"]);
    expect(metaState().past.at(-1)?.label).toBe("Renamed scene");
    expect(useProjectStore.temporal.getState().pastStates).toHaveLength(1);
    expect(saveProject).toHaveBeenCalledWith("talk", p, 5);
  });

  it("ignores an empty or unchanged title", () => {
    load(whole());
    const title = screen.getAllByLabelText("Scene title")[0];
    title.textContent = "  ";
    fireEvent.blur(title);
    expect(useProjectStore.getState().project!.scenes![0].title).toBe("Open");
    expect(title.textContent).toBe("Open");
    expect(saveProject).not.toHaveBeenCalled();
  });
});

describe("Script lens filler suggestion", () => {
  // "um" between w1 and w2, "uh" between w4 and w5.
  const FILLERS = [
    { w: "um", start: 1.65, end: 1.85, segIdx: 0 },
    { w: "uh", start: 4.7, end: 4.9, segIdx: 0 },
  ];

  it("marks the pending fillers and Apply cuts exactly those", async () => {
    const after = project([clip("a", 0, 49, 0), clip("b", 57, 140, 49), clip("c", 148, 210, 132)]);
    rippleCut.mockResolvedValue({ project: after, warnings: [] });
    saveProject.mockResolvedValue({ ok: true, revision: 6 });
    const { container } = load(whole(), doc(FILLERS));
    expect(screen.getByText(/I found 2 filler words across 2 scenes/)).toBeTruthy();
    const marks = [...container.querySelectorAll<HTMLElement>(".w.filler")];
    expect(marks.map((m) => [m.textContent, m.className])).toEqual([["um", "w filler sug"], ["uh", "w filler sug"]]);
    expect(marks[0].nextElementSibling?.getAttribute("data-word-idx")).toBe("2");
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(container.querySelectorAll(".w.filler.cut")).toHaveLength(2));
    expect(rippleCut).toHaveBeenCalledTimes(1);
    expect(rippleCut).toHaveBeenCalledWith("talk", expect.anything(), [{ startF: 49, endF: 57 }, { startF: 140, endF: 148 }]);
    expect(metaState().past.at(-1)?.label).toBe("Removed 2 fillers");
    expect(struck(container)).toEqual([]);
    expect(screen.queryByText(/filler words across/)).toBeNull();
  });

  it("hides the suggestion when there are no fillers, and on Dismiss", () => {
    const first = load(whole());
    expect(first.container.querySelector(".proposal")).toBeNull();
    cleanup();
    const { container } = load(whole(), doc(FILLERS));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(container.querySelector(".proposal")).toBeNull();
    expect(container.querySelectorAll(".w.filler.sug")).toHaveLength(0);
    expect(rippleCut).not.toHaveBeenCalled();
  });
});

describe("cue chip labels", () => {
  it("names library clips by their imported name, not their content hash", async () => {
    const { cuesByWord } = await import("../src/v3/story/script/script");
    const { assetLabels } = await import("../src/v3/story/drawer/library");
    const src = "workspaces/garden-1a2b3c/library/5e0123653faf01f9.png";
    const project = {
      fps: 30, width: 1920, height: 1080, name: "p", revision: 1, schemaVersion: 3,
      tracks: [
        { id: "speech", kind: "speech", clips: [{ id: "s", media: { source: "talk.mp4" }, srcInF: 0, srcOutF: 300, tlStartF: 0 }] },
        { id: "broll", kind: "video", clips: [{ id: "b", media: { source: src }, srcInF: 0, srcOutF: 60, tlStartF: 30,
          anchor: { mode: "word", ref: { media: "talk.mp4", wordIdx: 4 }, offsetF: 0 } }] },
      ],
    } as unknown as import("../src/types/project").Project;
    const labels = assetLabels([{ workspaceId: "garden-1a2b3c", workspaceName: "garden", asset: {
      id: "5e0123653faf01f9", sha256: "x", name: "garden-plate.png", kind: "image", file: "5e0123653faf01f9.png",
      bytes: 1, tags: [], createdAt: "" } }]);
    expect(cuesByWord(project, "talk.mp4", labels).get(4)?.[0].label).toBe("garden plate");
    expect(cuesByWord(project, "talk.mp4").get(4)?.[0].label).toBe("5e0123653faf01f9");
  });
});
