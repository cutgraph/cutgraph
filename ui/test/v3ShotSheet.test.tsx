// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import ShotChip from "../src/v3/story/shot/ShotChip";
import BoardLens from "../src/v3/story/lenses/BoardLens";
import { openShot, resetStory, useStory } from "../src/v3/story/storyStore";
import { undo, useProjectStore } from "../src/store/useProjectStore";
import { NO_CAST } from "../src/v3/library/cast";
import { resetCast, scopeKey, useCastStore } from "../src/v3/library/castStore";

const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "shot-demo", revision: 3, schemaVersion: 3,
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "c", media: { source: "data/source/talk.mp4" }, srcInF: 0, srcOutF: 210, tlStartF: 0 }] }],
  scenes: [
    { id: "s1", title: "Open", role: "Hook", startWord: 0 },
    { id: "s2", title: "Middle", role: "Beat", startWord: 2 },
    { id: "s3", title: "Close", role: "Wrap", startWord: 4, shot: { mode: "cameo", expression: "excited" } },
  ],
};

const shotOf = (id: string) => useProjectStore.getState().project?.scenes?.find((s) => s.id === id)?.shot;
const history = () => useProjectStore.temporal.getState().pastStates.length;
const group = (name: string) => screen.getByRole("group", { name });

beforeEach(() => {
  resetStory();
  resetCast();
  let revision = 3;
  saveProject.mockReset().mockImplementation(async () => ({ ok: true, revision: ++revision }));
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  useProjectStore.setState({ project: PROJECT, revision: 3, playheadF: 0, lastWarnings: [] });
  useProjectStore.temporal.getState().clear();
});
afterEach(cleanup);

async function openSheet(sceneId: string): Promise<void> {
  act(() => openShot(sceneId));
  render(<ShotSheet />);
  await screen.findByRole("dialog", { name: "Shot" });
}

describe("Shot view", () => {
  it("heads the sheet with the scene and starts footage scenes on Footage, without look pickers", async () => {
    await openSheet("s1");
    expect(screen.getByText("Shot · scene 1 · Hook")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Open" })).toBeTruthy();
    expect(within(group("Picture")).getByRole("button", { name: "Footage" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByRole("group", { name: "Expression" })).toBeNull();
    expect(screen.getByRole("button", { name: "Previous scene" }).hasAttribute("disabled")).toBe(true);
  });

  it("switches modes as one undo step each, saving every change", async () => {
    await openSheet("s1");
    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Animation" }));
    expect(shotOf("s1")).toEqual({ mode: "animation" });
    expect(history()).toBe(1);
    await waitFor(() => expect(saveProject).toHaveBeenCalledWith("shot-demo", expect.objectContaining({ scenes: expect.any(Array) }), 3));
    for (const name of ["Expression", "Pose", "Move", "Backdrop", "Diagram", "Lands on"]) expect(group(name)).toBeTruthy();

    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Cameo" }));
    expect(shotOf("s1")).toEqual({ mode: "cameo" });
    expect(screen.queryByRole("group", { name: "Backdrop" })).toBeNull();
    expect(group("Pose")).toBeTruthy();

    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Footage" }));
    expect(shotOf("s1")).toBeUndefined();
    expect(history()).toBe(3);
  });

  it("patches the shot from each picker and highlights the pick", async () => {
    await openSheet("s1");
    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Animation" }));
    const picks: [string, string, Record<string, unknown>][] = [
      ["Expression", "Excited", { expression: "excited" }],
      ["Pose", "Cheer", { pose: "cheer" }],
      ["Move", "Hop in", { move: "enter" }],
      ["Backdrop", "Route", { backdrop: "route" }],
      ["Diagram", "Checks tick", { diagram: "checks" }],
      ["Lands on", "w1", { cueWord: 1 }],
    ];
    let expected: Record<string, unknown> = { mode: "animation" };
    for (const [row, name, patch] of picks) {
      fireEvent.click(within(group(row)).getByRole("button", { name }));
      expected = { ...expected, ...patch };
      expect(shotOf("s1")).toEqual(expected);
      expect(within(group(row)).getByRole("button", { name }).getAttribute("aria-pressed")).toBe("true");
    }
    expect(history()).toBe(1 + picks.length);
    act(() => undo());
    expect(shotOf("s1")).not.toHaveProperty("cueWord");
  });

  it("lets a cameo pick the word it springs in on", async () => {
    await openSheet("s3");
    expect(screen.getByText(/springs into the lower corner over your footage, on its “Lands on” word/)).toBeTruthy();
    expect(within(group("Lands on")).getByRole("button", { name: "w4" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(within(group("Lands on")).getByRole("button", { name: "w5" }));
    expect(shotOf("s3")).toEqual({ mode: "cameo", expression: "excited", cueWord: 5 });
    expect(within(group("Lands on")).getByRole("button", { name: "w5" }).getAttribute("aria-pressed")).toBe("true");
    expect(history()).toBe(1);
  });

  it("edits the headline inline as one step on Enter, and Escape abandons the draft", async () => {
    await openSheet("s1");
    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Animation" }));
    const input = screen.getByRole("textbox", { name: "Headline" }) as HTMLInputElement;
    expect(input.placeholder).toBe("Open");
    input.focus();
    fireEvent.change(input, { target: { value: "  Ship it  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    expect(shotOf("s1")).toEqual({ mode: "animation", headline: "Ship it" });
    expect(history()).toBe(2);

    fireEvent.change(input, { target: { value: "Nope" } });
    fireEvent.keyDown(input, { key: "Escape" });
    fireEvent.blur(input);
    expect(shotOf("s1")?.headline).toBe("Ship it");
    expect(useStory.getState().shotSceneId).toBe("s1");
  });

  it("turns the headline (and eyebrow) off with Show headline, on by default", async () => {
    await openSheet("s1");
    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Animation" }));
    const toggle = screen.getByRole("button", { name: "Show headline" });
    expect(toggle.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(toggle);
    expect(shotOf("s1")).toEqual({ mode: "animation", hideHeadline: true });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(toggle);
    expect(shotOf("s1")).toEqual({ mode: "animation" });
    expect(history()).toBe(3);
  });

  it("sets the eyebrow over the headline: the project's brand by default, the shot's own, or hidden", async () => {
    const cast = { ...NO_CAST, brand: { name: "WEEKNIGHT PASTA", accent: "#2bb3a3" } };
    useCastStore.setState({ scopes: { [scopeKey({ workspace: null })]: { cast, status: "ready", error: null } } });
    await openSheet("s1");
    expect(screen.queryByRole("textbox", { name: "Eyebrow" })).toBeNull();
    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Animation" }));
    const input = screen.getByRole("textbox", { name: "Eyebrow" }) as HTMLInputElement;
    const hide = screen.getByRole("button", { name: "Hide" });
    expect([input.value, input.placeholder, input.style.color, hide.getAttribute("aria-pressed")]).toEqual(["", "WEEKNIGHT PASTA", "rgb(43, 179, 163)", "false"]);

    input.focus();
    fireEvent.change(input, { target: { value: "  TEAM NOTES " } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    expect(shotOf("s1")).toEqual({ mode: "animation", eyebrow: "TEAM NOTES" });
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    expect(shotOf("s1")).toEqual({ mode: "animation" });

    fireEvent.click(hide);
    expect(shotOf("s1")).toEqual({ mode: "animation", eyebrow: "" });
    expect([input.disabled, input.placeholder, hide.getAttribute("aria-pressed")]).toEqual([true, "Hidden", "true"]);
    fireEvent.click(hide);
    expect(shotOf("s1")).toEqual({ mode: "animation" });
    expect(history()).toBe(5);
    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Cameo" }));
    expect(screen.queryByRole("textbox", { name: "Eyebrow" })).toBeNull();
  });

  it("shows the scene's code, moves between scenes and closes on Escape", async () => {
    await openSheet("s2");
    fireEvent.click(screen.getByRole("button", { name: "Next scene" }));
    expect(useStory.getState().shotSceneId).toBe("s3");
    expect(await screen.findByText("Shot · scene 3 · Wrap")).toBeTruthy();
    expect(within(group("Expression")).getByRole("button", { name: "Excited" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "View code" }));
    const code = screen.getByLabelText("Scene code").textContent ?? "";
    expect(code).toContain('<Scene mode="cameo">');
    expect(code).toContain('<Character cameo pose="wave" expression="excited" move="wave" />');
    expect(code).not.toContain('"mode":');
    fireEvent.keyDown(window, { key: "Escape" });
    expect(useStory.getState().shotSceneId).toBeNull();
  });
});

describe("Shot affordances", () => {
  it("offers the Shot view from a Script scene header, naming the default rig's expression once animated", () => {
    render(<><ShotChip scene={PROJECT.scenes![0]} /><ShotChip scene={PROJECT.scenes![2]} /></>);
    const [footage, cameo] = screen.getAllByRole("button");
    expect(footage.textContent).toBe("✦ Shot");
    expect(cameo.textContent).toBe("✦ Excited");
    fireEvent.click(cameo);
    expect(useStory.getState().shotSceneId).toBe("s3");
  });

  it("marks animated Board panels and opens their shot on double-click", async () => {
    render(<BoardLens />);
    await screen.findByText("w0 w1");
    const panels = Array.from(document.querySelectorAll<HTMLElement>(".panel"));
    expect(panels.map((p) => p.querySelector(".cue-anim")?.textContent ?? null)).toEqual([null, null, "✦ Excited"]);
    fireEvent.doubleClick(panels[0]);
    expect(useStory.getState()).toMatchObject({ lens: "script", shotSceneId: null });
    fireEvent.doubleClick(panels[2]);
    expect(useStory.getState().shotSceneId).toBe("s3");
  });
});
