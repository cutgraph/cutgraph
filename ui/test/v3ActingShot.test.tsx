// @vitest-environment jsdom
// Acting in the Shot view: a bubble picker for the lead, the co-star and each ensemble member, and
// the ensemble itself (up to three more characters from the video's cast), each pick one undo step. The
// player spec carries every member resolved against the cast.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ENSEMBLE_MAX } from "motion-core";
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

import ShotSheet from "../src/v3/story/shot/ShotSheet";
import { animatedSceneSpecs } from "../src/v3/story/shot/specs";
import { sceneSpans, speechMedia } from "../src/v3/story/model";
import { openShot, resetStory } from "../src/v3/story/storyStore";
import { undo, useProjectStore } from "../src/store/useProjectStore";
import type { CharacterProfile } from "../src/v3/api";
import type { Cast } from "../src/v3/library/cast";
import { resetCast, scopeKey, useCastStore } from "../src/v3/library/castStore";

const profile = (id: string, name: string, rig: string, palette: string, extra: Partial<CharacterProfile> = {}): CharacterProfile => ({
  id, name, rig, art: [], palette, expressions: [], workspaceId: rig === "lens" ? "brand" : "garden-1", createdAt: "t", approved: true, ...extra,
});
const DEFAULT_PROFILES = profile("default-character", "Lens", "lens", "periwinkle");
const GUIDE = profile("notes-guest", "Guide", "lens", "periwinkle", { workspaceId: "garden-1", expressions: [{ id: "focus", name: "Quiet focus", spec: { eyes: "narrow", mouth: "round" }, createdAt: "t" }] });
const EDITOR = profile("editor", "Editor", "lens", "periwinkle", { workspaceId: "garden-1" });
const REVIEWER = profile("reviewer", "Reviewer", "lens", "periwinkle", { workspaceId: "garden-1", approved: false });
const CAST: Cast = { characters: [DEFAULT_PROFILES, GUIDE, EDITOR, REVIEWER], default: "default-character", workspace: { id: "garden-1", name: "Garden" } };

const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "acting-demo", revision: 3, schemaVersion: 3, workspaceId: "garden-1",
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "c", media: { source: "data/source/talk.mp4" }, srcInF: 0, srcOutF: 210, tlStartF: 0 }] }],
  scenes: [
    { id: "s1", title: "Open", role: "Hook", startWord: 0, shot: { mode: "animation", cueWord: 1, costar: { character: "notes-guest" } } },
    { id: "s2", title: "Middle", role: "Beat", startWord: 2, shot: { mode: "cameo" } },
    { id: "s3", title: "Close", role: "Wrap", startWord: 4 },
  ],
};

const shotOf = (id: string) => useProjectStore.getState().project?.scenes?.find((s) => s.id === id)?.shot;
const history = () => useProjectStore.temporal.getState().pastStates.length;
const group = (name: string) => screen.getByRole("group", { name });
const pressed = (row: string, name: string) => within(group(row)).getByRole("button", { name }).getAttribute("aria-pressed");
const tap = (row: string, name: string) => fireEvent.click(within(group(row)).getByRole("button", { name }));

function useProject(project: Project): void {
  useProjectStore.setState({ project, revision: 3, playheadF: 0, lastWarnings: [] });
  useProjectStore.temporal.getState().clear();
}

async function openSheet(sceneId: string): Promise<void> {
  act(() => openShot(sceneId));
  render(<ShotSheet />);
  await screen.findByRole("dialog", { name: "Shot" });
}

beforeEach(() => {
  resetStory();
  resetCast();
  useCastStore.setState({ scopes: { [scopeKey({ workspace: "garden-1" })]: { cast: CAST, status: "ready", error: null } } });
  let revision = 3;
  saveProject.mockReset().mockImplementation(async () => ({ ok: true, revision: ++revision }));
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  useProject(PROJECT);
});
afterEach(cleanup);

describe("Shot view: bubbles", () => {
  it("gives the lead and the co-star a bubble each, one undo step per pick; None takes it off", async () => {
    await openSheet("s1");
    expect(within(group("Bubble")).getAllByRole("button").map((b) => b.textContent)).toEqual(["None", "Think", "Say", "Exclaim", "Question", "!!"]);
    expect(pressed("Bubble", "None")).toBe("true");
    tap("Bubble", "Think");
    expect(shotOf("s1")).toEqual({ ...PROJECT.scenes![0].shot, bubble: "think" });
    expect(pressed("Bubble", "Think")).toBe("true");
    tap("Co-star bubble", "Say");
    expect(shotOf("s1")?.costar).toEqual({ character: "notes-guest", bubble: "say" });
    expect(history()).toBe(2);
    tap("Co-star bubble", "None");
    tap("Bubble", "None");
    expect(shotOf("s1")).toEqual(PROJECT.scenes![0].shot);
    act(() => undo());
    expect(shotOf("s1")?.bubble).toBe("think");
  });

  it("a cameo has the lead's bubble only; footage has none", async () => {
    await openSheet("s2");
    tap("Bubble", "Exclaim");
    expect(shotOf("s2")).toEqual({ mode: "cameo", bubble: "exclaim" });
    expect(screen.queryByRole("group", { name: "Co-star bubble" })).toBeNull();
    expect(screen.queryByRole("button", { name: "+ Add to ensemble" })).toBeNull();
    cleanup();
    await openSheet("s3");
    expect(screen.queryByRole("group", { name: "Bubble" })).toBeNull();
  });
});

describe("Shot view: ensemble", () => {
  it("adds the cast's next characters after the lead and co-star, up to three, and directs each one", async () => {
    await openSheet("s1");
    fireEvent.click(screen.getByRole("button", { name: "+ Add to ensemble" }));
    expect(shotOf("s1")?.ensemble).toEqual([{ character: "editor" }]);
    expect(pressed("Ensemble 1", "Editor")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "+ Add to ensemble" }));
    expect(shotOf("s1")?.ensemble).toEqual([{ character: "editor" }, { character: "reviewer" }]);
    expect(history()).toBe(2);
    expect(within(screen.getByRole("group", { name: "Ensemble 2" }).parentElement!).getByText("Draft")).toBeTruthy();

    tap("Ensemble 1 pose", "Wave");
    tap("Ensemble 1 move", "Sway");
    tap("Ensemble 2 bubble", "Exclaim");
    tap("Ensemble 2 expression", "Happy");
    expect(shotOf("s1")?.ensemble).toEqual([
      { character: "editor", pose: "wave", move: "sway" },
      { character: "reviewer", bubble: "exclaim", expression: "happy" },
    ]);
    expect(history()).toBe(6);
    expect([pressed("Ensemble 1 pose", "Wave"), pressed("Ensemble 2 bubble", "Exclaim")]).toEqual(["true", "true"]);

    fireEvent.click(screen.getByRole("button", { name: "+ Add to ensemble" }));
    expect(shotOf("s1")?.ensemble).toHaveLength(ENSEMBLE_MAX);
    expect(screen.queryByRole("button", { name: "+ Add to ensemble" })).toBeNull();
  });

  it("recasts a member, dropping a face its new character can't wear, and removes members one step each", async () => {
    useProject({ ...PROJECT, scenes: [{ ...PROJECT.scenes![0], shot: { mode: "animation", interaction: "fistbump", costar: { character: "notes-guest" },
      ensemble: [{ character: "notes-guest", expression: "focus", bubble: "say" }, { character: "editor" }] } }, ...PROJECT.scenes!.slice(1)] });
    await openSheet("s1");
    expect(screen.getByText("An ensemble stands side by side: the interaction plays only in a duo.")).toBeTruthy();
    expect(within(group("Ensemble 1 expression")).getAllByRole("button").map((b) => b.textContent)).toContain("Quiet focus");
    tap("Ensemble 1", "Lens");
    expect(shotOf("s1")?.ensemble?.[0]).toEqual({ character: "default-character", bubble: "say" });
    const remove = (n: number) => fireEvent.click(within(group(`Ensemble ${n}`).parentElement!).getByRole("button", { name: "Remove" }));
    remove(1);
    expect(shotOf("s1")?.ensemble).toEqual([{ character: "editor" }]);
    remove(1);
    expect(shotOf("s1")).toEqual({ mode: "animation", interaction: "fistbump", costar: { character: "notes-guest" } });
    expect(history()).toBe(3);
    act(() => undo());
    expect(shotOf("s1")?.ensemble).toEqual([{ character: "editor" }]);
  });

  it("the player spec draws every member resolved against the cast, bubbles and all", () => {
    const project: Project = { ...PROJECT, scenes: [{ ...PROJECT.scenes![0], shot: { mode: "animation", bubble: "think",
      costar: { character: "notes-guest", bubble: "exclaim" }, ensemble: [{ character: "editor", move: "enter", bubble: "say" }, { character: "reviewer" }] } },
    ...PROJECT.scenes!.slice(1)] };
    const media = speechMedia(project);
    const [spec] = animatedSceneSpecs(project, WORDS, sceneSpans(project, media, WORDS), media, CAST);
    expect([spec.shot.bubble, spec.shot.costar?.bubble, spec.shot.costar?.palette]).toEqual(["think", "exclaim", "periwinkle"]);
    expect(spec.shot.ensemble?.map((m) => [m.character, m.palette, m.move, m.bubble])).toEqual([
      ["lens", "periwinkle", "enter", "say"], ["lens", "periwinkle", "idle", undefined],
    ]);
  });
});
