// @vitest-environment jsdom
// The video's length after a reorder, cut or restore. A word-anchored cue follows its word but
// never lengthens the video, so moving the scene that carries a music bed can't add a silent tail. The
// preview's rule (projectDurationF) is held to the export's (pipeline/edits.py timeline_end_f) by the
// shared fixture, whose edit cases are the real verbs' output (tests/test_timeline_length.py).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { Project } from "../src/types/project";

const { saveProject, rippleCut, rippleRestore } = vi.hoisted(() => ({
  saveProject: vi.fn(),
  rippleCut: vi.fn(),
  rippleRestore: vi.fn(),
}));
vi.mock("../src/api", () => ({ api: { saveProject, rippleCut, rippleRestore } }));

import ToastHost from "../src/v3/Toast";
import { reorderScenes } from "../src/v3/story/board/reorder";
import { acceptSourceCuts, restoreSourceRanges } from "../src/transcript/acceptCuts";
import { playedF, projectDurationF } from "../src/timeline/geometry";
import { applyLocal, useProjectStore } from "../src/store/useProjectStore";

interface Case { name: string; project: Project; endF: number; rawEndF: number }
const CASES = (JSON.parse(readFileSync(resolve(__dirname, "../../tests/fixtures/timeline_length_parity.json"), "utf-8")) as { cases: Case[] }).cases;
const fixture = (name: string): Project => structuredClone(CASES.find((c) => c.name === name)!.project);

const SRC = "data/source/talk.mp4";
const ORIGINAL = fixture("music-on-word-0");
const MOVED = fixture("reorder-moves-the-music-word");
const CUT = fixture("cut-shortens-the-speech");
const RESTORED = fixture("restore-plays-the-bed-whole");

const speechEndF = (p: Project) =>
  Math.max(...p.tracks.filter((t) => t.kind === "speech").flatMap((t) => t.clips ?? []).map((c) => c.tlStartF + c.srcOutF - c.srcInF));
const bed = (p: Project) => p.tracks.find((t) => t.kind === "audio")!.clips![0];
const past = () => useProjectStore.temporal.getState().pastStates.length;

const fetchMock = vi.fn();

beforeEach(() => {
  saveProject.mockReset();
  rippleCut.mockReset();
  rippleRestore.mockReset();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  useProjectStore.setState({ project: ORIGINAL, revision: 7, playheadF: 0, lastWarnings: [] });
  useProjectStore.temporal.getState().clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the video's length", () => {
  it("matches the export's rule on every shared case", () => {
    for (const c of CASES) expect([c.name, projectDurationF(c.project)]).toEqual([c.name, c.endF]);
  });

  it("plays only the part of a cue before the end", () => {
    expect(playedF(MOVED, bed(MOVED).tlStartF, 207)).toBe(60);
    expect(playedF(ORIGINAL, bed(ORIGINAL).tlStartF, 207)).toBe(207);
    expect(playedF(MOVED, 240, 30)).toBe(0);
  });
});

describe("reorder, cut and restore keep the video the speech's length", () => {
  it("encodes the video identifier as one reorder URL segment", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ project: MOVED, warnings: [] }) });
    saveProject.mockResolvedValue({ ok: true, project: MOVED, revision: 8 });
    expect(await reorderScenes("video/other?#%", ["s2", "s3", "s1"], "Moved scene")).toBe(true);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/verbs/reorder_scenes/video%2Fother%3F%23%25");
  });
  it("moving the scene that carries the music bed (the overlapping scene case)", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ project: MOVED, warnings: [] }) });
    saveProject.mockResolvedValue({ ok: true, project: MOVED, revision: 8 });
    expect(await reorderScenes("timeline-length", ["s2", "s3", "s1"], "Moved “Open”")).toBe(true);

    const project = useProjectStore.getState().project!;
    expect(bed(project).tlStartF).toBe(150);
    expect(bed(project).srcOutF - bed(project).srcInF).toBe(207);
    expect(projectDurationF(project)).toBe(speechEndF(project));
    expect(projectDurationF(project)).toBe(projectDurationF(ORIGINAL));
    expect(projectDurationF(project)).toBe(210);
  });

  it("cutting under the bed, then restoring the cut", async () => {
    rippleCut.mockResolvedValue({ project: CUT, warnings: [] });
    saveProject.mockResolvedValue({ ok: true, project: CUT, revision: 8 });
    const cut = { media: SRC, srcStartF: 63, srcEndF: 123, kind: "selection" as const, durSec: 2 };
    expect(await acceptSourceCuts("timeline-length", [cut], 30)).toBe(true);
    expect(rippleCut.mock.calls[0][2]).toEqual([{ startF: 63, endF: 123 }]);
    let project = useProjectStore.getState().project!;
    expect(projectDurationF(project)).toBe(speechEndF(project));
    expect(projectDurationF(project)).toBe(150);

    rippleRestore.mockResolvedValue({ project: RESTORED, warnings: [] });
    saveProject.mockResolvedValue({ ok: true, project: RESTORED, revision: 9 });
    expect(await restoreSourceRanges("timeline-length", [{ media: SRC, srcStartF: 63, srcEndF: 123 }], "Restored")).toBe(true);
    project = useProjectStore.getState().project!;
    expect(projectDurationF(project)).toBe(speechEndF(project));
    expect(playedF(project, bed(project).tlStartF, 207)).toBe(207);
  });
});

describe("the reorder toast", () => {
  async function move(): Promise<void> {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ project: MOVED, warnings: [] }) });
    saveProject.mockResolvedValue({ ok: true, project: MOVED, revision: 8 });
    render(<ToastHost />);
    await act(async () => {
      await reorderScenes("timeline-length", ["s2", "s3", "s1"], "Moved “Open”");
    });
    expect(screen.getByRole("status").textContent).toContain("Moved “Open”");
  }

  it("undoes the move as one step and saves it", async () => {
    await move();
    expect(past()).toBe(1);
    saveProject.mockResolvedValue({ ok: true, project: ORIGINAL, revision: 9 });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    });
    expect(useProjectStore.getState().project).toEqual(ORIGINAL);
    expect(past()).toBe(0);
    expect(saveProject).toHaveBeenLastCalledWith("timeline-length", ORIGINAL, 8);
    expect(useProjectStore.getState().revision).toBe(9);
  });

  it("leaves a later edit alone", async () => {
    await move();
    const later = { ...MOVED, title: "Renamed" };
    applyLocal(() => ({ project: later }), { origin: "human", label: "Renamed" });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(useProjectStore.getState().project).toBe(later);
    expect(past()).toBe(2);
    expect(screen.getByRole("status").textContent).toContain("Use Undo in the top bar");
  });
});
