// @vitest-environment jsdom
// Each staging control in the Shot view creates one undo step.
// Coverage includes cameras, diagrams, bubbles and ensemble labels.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { TranscriptWord } from "../src/api";
import type { Project } from "../src/types/project";
import type { Asset, WorkspaceDetail } from "../src/v3/api";

const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ w: `w${i}`, start: i + 0.1, end: i + 0.6, segIdx: 0 }));
const { saveProject, workspace } = vi.hoisted(() => ({ saveProject: vi.fn(), workspace: vi.fn() }));
vi.mock("../src/api", () => ({
  api: {
    transcription: async () => ({ state: "ready", job: null }),
    getTranscript: async () => ({ words: WORDS, segments: [], fillers: [] }),
    saveProject,
  },
}));
vi.mock("../src/v3/api", () => ({
  v3api: {
    workspace: (id: string) => workspace(id),
    assetThumbUrl: (ws: string, id: string) => `/api/workspaces/${ws}/assets/${id}/thumb`,
  },
}));
vi.mock("../src/toast", () => ({ toast: () => {} }));

import ShotSheet from "../src/v3/story/shot/ShotSheet";
import { resetLibraries } from "../src/v3/story/shot/screenLibrary";
import { openShot, resetStory } from "../src/v3/story/storyStore";
import { undo, useProjectStore } from "../src/store/useProjectStore";
import { NO_CAST } from "../src/v3/library/cast";
import { resetCast, scopeKey, useCastStore } from "../src/v3/library/castStore";

const asset = (id: string, name: string, kind: Asset["kind"], ext: string): Asset => ({
  id, sha256: id, name, kind, file: `${id}.${ext}`, bytes: 10, tags: [], createdAt: "t",
});
const NOTES = asset("a1b2c3d4e5f60718", "notes.png", "image", "png");
const PROMO = asset("9999aaaa0000bbbb", "sample-clip.mp4", "video", "mp4");
let brandAssets: Asset[] = [];

const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "stage-demo", revision: 3, schemaVersion: 3,
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "c", media: { source: "data/source/talk.mp4" }, srcInF: 0, srcOutF: 210, tlStartF: 0 }] }],
  scenes: [
    { id: "s1", title: "Open", role: "Hook", startWord: 0, shot: { mode: "animation" } },
    { id: "s4", title: "Payoff", role: "Payoff", startWord: 1, shot: { mode: "animation", costar: { character: "default-character" }, ensemble: [{ character: "default-character" }, { character: "default-character" }] } },
    { id: "s2", title: "Middle", role: "Beat", startWord: 2, shot: { mode: "cameo" } },
    { id: "s3", title: "Close", role: "Wrap", startWord: 4 },
  ],
};

const shotOf = (id: string) => useProjectStore.getState().project?.scenes?.find((s) => s.id === id)?.shot;
const history = () => useProjectStore.temporal.getState().pastStates.length;
const group = (name: string) => screen.getByRole("group", { name });
const press = (row: string, name: string) => fireEvent.click(within(group(row)).getByRole("button", { name }));
const pressed = (row: string, name: string) => within(group(row)).getByRole("button", { name }).getAttribute("aria-pressed");

function useProject(project: Project): void {
  useProjectStore.setState({ project, revision: 3, playheadF: 0, lastWarnings: [] });
  useProjectStore.temporal.getState().clear();
}

beforeEach(() => {
  resetStory();
  resetCast();
  resetLibraries();
  useCastStore.setState({ scopes: { [scopeKey({ workspace: null })]: { cast: NO_CAST, status: "ready", error: null } } });
  let revision = 3;
  saveProject.mockReset().mockImplementation(async () => ({ ok: true, revision: ++revision }));
  brandAssets = [NOTES, PROMO];
  workspace.mockReset().mockImplementation(async (id: string): Promise<WorkspaceDetail> => ({
    workspace: { id, name: id === "brand" ? "Brand" : id, createdAt: "", updatedAt: "", videoCount: 1, assetCount: brandAssets.length },
    videos: [],
    assets: id === "brand" ? brandAssets : [],
  }));
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  useProject(PROJECT);
});
afterEach(cleanup);

async function openSheet(sceneId: string): Promise<void> {
  act(() => openShot(sceneId));
  render(<ShotSheet />);
  await screen.findByRole("dialog", { name: "Shot" });
}

describe("Shot view: short-film staging", () => {
  it("offers the Pan camera, independent diagrams and the \"?\" bubble", async () => {
    await openSheet("s1");
    press("Camera", "Pan");
    expect(shotOf("s1")?.camera).toBe("pan");
    expect(screen.getByText(/carrying the stage in from the left/)).toBeTruthy();
    const diagrams = within(group("Diagram")).getAllByRole("button").map((b) => b.textContent);
    expect(diagrams.slice(-6)).toEqual(["No diagram", "Grid", "Chain", "Radial", "Tree", "List"]);
    press("Diagram", "Grid");
    press("Bubble", "Question");
    expect(shotOf("s1")).toEqual({ mode: "animation", camera: "pan", diagram: "grid", bubble: "question" });
    expect(history()).toBe(3);
    act(() => undo());
    expect(shotOf("s1")?.bubble).toBeUndefined();
  });

  it("offers the neutral \"!!\" as \"!!\" for the co-star, one undo step", async () => {
    await openSheet("s4");
    press("Co-star bubble", "!!");
    expect(shotOf("s4")?.costar).toEqual({ character: "default-character", bubble: "exclaims" });
    expect(pressed("Co-star bubble", "!!")).toBe("true");
    expect(history()).toBe(1);
    act(() => undo());
    expect(shotOf("s4")?.costar).toEqual({ character: "default-character" });
  });

  it("names the independent diagram's boxes on a diagram: six available labels, each commit one undo step", async () => {
    await openSheet("s1");
    expect(screen.queryByRole("textbox", { name: "Board label node 1" })).toBeNull();
    press("Diagram", "Chain");
    const input = (name: string) => screen.getByRole("textbox", { name: `Board label ${name}` }) as HTMLInputElement;
    expect(["node 1", "node 2", "node 3", "node 4", "node 5", "node 6"].map((n) => input(n).maxLength)).toEqual([12, 12, 12, 12, 12, 12]);
    const type = (name: string, value: string) => {
      fireEvent.change(input(name), { target: { value } });
      fireEvent.blur(input(name));
    };
    type("node 1", "NOTES");
    expect(shotOf("s1")).toEqual({ mode: "animation", diagram: "chain", boardLabels: ["NOTES"] });
    type("node 3", " Sharing ");
    expect(shotOf("s1")?.boardLabels).toEqual(["NOTES", "", "Sharing"]);
    expect(history()).toBe(3);
    act(() => undo());
    expect(shotOf("s1")?.boardLabels).toEqual(["NOTES"]);
    type("node 1", "");
    expect(shotOf("s1")?.boardLabels).toBeUndefined();
    press("Diagram", "Tools scatter");
    expect(screen.queryByRole("textbox", { name: "Board label node 1" })).toBeNull();
  });

  it("places a stage's labels: the headline over the co-star, badges under the ensemble", async () => {
    await openSheet("s1");
    expect(screen.queryByRole("group", { name: "Headline place" })).toBeNull();
    cleanup();
    await openSheet("s4");
    expect(pressed("Headline place", "Its own")).toBe("true");
    press("Headline place", "Over the co-star");
    expect(shotOf("s4")?.headlineAt).toBe("costar");
    expect(screen.getByText(/one under each ensemble member/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "+ Add badge" }));
    const text = screen.getByRole("textbox", { name: "Badge 1" });
    fireEvent.change(text, { target: { value: "NOTES" } });
    fireEvent.blur(text);
    expect(shotOf("s4")?.badges).toEqual([{ text: "NOTES", tone: "ink" }]);
    press("Headline place", "Its own");
    expect(shotOf("s4")?.headlineAt).toBeUndefined();
    press("Layout", "Title card");
    expect(screen.queryByRole("group", { name: "Headline place" })).toBeNull();
  });
});
