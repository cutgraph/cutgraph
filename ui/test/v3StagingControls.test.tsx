// @vitest-environment jsdom
// Short-film staging in the Shot view: an animation's Layout and Camera pickers and each layout's
// own fields (the phones' library images, imported on the spot, in up to three slots; the title
// card's badges; the sign-off's letters), every pick one undo step; the controls stay out of footage and
// cameo scenes, and the player's spec shows the picked images.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { TranscriptWord } from "../src/api";
import type { Project } from "../src/types/project";
import type { Asset, WorkspaceDetail } from "../src/v3/api";

const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ w: `w${i}`, start: i + 0.1, end: i + 0.6, segIdx: 0 }));
const { saveProject, uploadAssets, workspace } = vi.hoisted(() => ({ saveProject: vi.fn(), uploadAssets: vi.fn(), workspace: vi.fn() }));
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
    uploadAssets: (id: string, files: File[]) => uploadAssets(id, files),
    assetThumbUrl: (ws: string, id: string) => `/api/workspaces/${ws}/assets/${id}/thumb`,
  },
}));
vi.mock("../src/toast", () => ({ toast: () => {} }));

import ShotSheet from "../src/v3/story/shot/ShotSheet";
import { animatedSceneSpecs } from "../src/v3/story/shot/specs";
import { refreshLibraries, resetLibraries, useScreenAssets } from "../src/v3/story/shot/screenLibrary";
import { sceneSpans, speechMedia } from "../src/v3/story/model";
import { openShot, resetStory } from "../src/v3/story/storyStore";
import { undo, useProjectStore } from "../src/store/useProjectStore";
import { NO_CAST } from "../src/v3/library/cast";
import { resetCast, scopeKey, useCastStore } from "../src/v3/library/castStore";

const asset = (id: string, name: string, kind: Asset["kind"], ext: string): Asset => ({
  id, sha256: id, name, kind, file: `${id}.${ext}`, bytes: 10, tags: [], createdAt: "t",
});
const NOTES = asset("a1b2c3d4e5f60718", "notes.png", "image", "png");
const PROMO = asset("9999aaaa0000bbbb", "rainfall.mp4", "video", "mp4");
const EVENTS = asset("0f1e2d3c4b5a6978", "courtyard.jpg", "image", "jpg");
const LOCK = asset("1234567890abcdef", "floor-map.png", "image", "png");
let brandAssets: Asset[] = [];

const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "stage-demo", revision: 3, schemaVersion: 3,
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "c", media: { source: "data/source/talk.mp4" }, srcInF: 0, srcOutF: 210, tlStartF: 0 }] }],
  scenes: [
    { id: "s1", title: "Open", role: "Hook", startWord: 0, shot: { mode: "animation" } },
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
  uploadAssets.mockReset();
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  useProject(PROJECT);
});
afterEach(cleanup);

async function openSheet(sceneId: string): Promise<void> {
  act(() => openShot(sceneId));
  render(<ShotSheet />);
  await screen.findByRole("dialog", { name: "Shot" });
}

describe("Shot view: staging", () => {
  it("stages animations only, starting on the character stage with a still camera", async () => {
    await openSheet("s2");
    expect(screen.queryByRole("group", { name: "Layout" })).toBeNull();
    cleanup();
    await openSheet("s3");
    expect(screen.queryByRole("group", { name: "Camera" })).toBeNull();
    cleanup();
    await openSheet("s1");
    expect(pressed("Layout", "Stage")).toBe("true");
    expect(pressed("Camera", "Still")).toBe("true");
    expect(workspace).not.toHaveBeenCalled();
  });

  it("picks a layout and a camera, one undo step each, and Stage and Still clear them", async () => {
    await openSheet("s1");
    press("Layout", "Sign-off");
    press("Camera", "Push in");
    expect(shotOf("s1")).toEqual({ mode: "animation", layout: "signoff", camera: "push-in" });
    expect(history()).toBe(2);
    expect(screen.getByText(/bounded centre-framed zoom/)).toBeTruthy();
    press("Camera", "Pull out");
    expect(shotOf("s1")?.camera).toBe("pull-out");
    press("Camera", "Still");
    press("Layout", "Stage");
    expect(shotOf("s1")).toEqual({ mode: "animation" });
    act(() => undo());
    expect(shotOf("s1")).toEqual({ mode: "animation", layout: "signoff" });
    expect(pressed("Layout", "Sign-off")).toBe("true");
  });

  it("replaces a pending badge draft with the stored text after an undo", async () => {
    await openSheet("s1");
    press("Layout", "Title card");
    fireEvent.click(screen.getByRole("button", { name: "+ Add badge" }));
    const text = () => screen.getByRole("textbox", { name: "Badge 1" }) as HTMLInputElement;
    const added = shotOf("s1")?.badges?.[0].text;
    fireEvent.change(text(), { target: { value: "NOTES" } });
    fireEvent.blur(text());
    expect(shotOf("s1")?.badges?.[0].text).toBe("NOTES");
    fireEvent.change(text(), { target: { value: "PENDING" } });
    act(() => undo());
    expect(shotOf("s1")?.badges?.[0].text).toBe(added);
    expect(text().value).toBe(added);
  });

  it("edits a title card's badges: add, rename, tone, remove, never more than four", async () => {
    await openSheet("s1");
    press("Layout", "Title card");
    expect(screen.getByText("No badges yet: the headline stands alone.")).toBeTruthy();
    const add = screen.getByRole("button", { name: "+ Add badge" });
    fireEvent.click(add);
    const text = screen.getByRole("textbox", { name: "Badge 1" });
    fireEvent.change(text, { target: { value: "NOTES" } });
    fireEvent.blur(text);
    fireEvent.click(add);
    fireEvent.change(screen.getByRole("textbox", { name: "Badge 2" }), { target: { value: "INDEX" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Badge 2" }), { key: "Enter" });
    fireEvent.blur(screen.getByRole("textbox", { name: "Badge 2" }));
    press("Badge 2 tone", "Paper");
    expect(shotOf("s1")?.badges).toEqual([{ text: "NOTES", tone: "ink" }, { text: "INDEX", tone: "paper" }]);
    expect(pressed("Badge 2 tone", "Paper")).toBe("true");
    const escaped = screen.getByRole("textbox", { name: "Badge 1" });
    fireEvent.change(escaped, { target: { value: "NOPE" } });
    fireEvent.keyDown(escaped, { key: "Escape" });
    expect(shotOf("s1")?.badges?.[0].text).toBe("NOTES");
    fireEvent.click(add);
    fireEvent.click(add);
    expect(shotOf("s1")?.badges).toHaveLength(4);
    expect((add as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Remove badge 4" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove badge 3" }));
    const cleared = screen.getByRole("textbox", { name: "Badge 2" });
    fireEvent.change(cleared, { target: { value: "  " } });
    fireEvent.blur(cleared);
    expect(shotOf("s1")?.badges).toEqual([{ text: "NOTES", tone: "ink" }]);
    fireEvent.click(screen.getByRole("button", { name: "Remove badge 1" }));
    expect(shotOf("s1")).toEqual({ mode: "animation", layout: "title" });
  });

  it("sets a sign-off's letters in upper case, refuses what the pixel face can't draw, and empty leaves no mark", async () => {
    await openSheet("s1");
    press("Layout", "Sign-off");
    const letters = screen.getByRole("textbox", { name: "Letters" });
    expect(letters.getAttribute("placeholder")).toBe("Optional text");
    fireEvent.change(letters, { target: { value: "ab" } });
    fireEvent.blur(letters);
    expect(shotOf("s1")?.mark).toBe("AB");
    fireEvent.change(letters, { target: { value: "A!" } });
    fireEvent.blur(letters);
    expect(shotOf("s1")?.mark).toBe("AB");
    expect(screen.getByText("Up to 4 letters, digits or &.")).toBeTruthy();
    fireEvent.change(letters, { target: { value: "" } });
    fireEvent.blur(letters);
    expect(shotOf("s1")).not.toHaveProperty("mark");
  });

  it("puts a library image on the phone, or the generated app screen, and imports one on the spot", async () => {
    await openSheet("s1");
    press("Layout", "Phone screen");
    await waitFor(() => expect(within(group("Phone image")).getAllByRole("button")).toHaveLength(2));
    expect(workspace).toHaveBeenCalledWith("brand");
    expect(pressed("Phone image", "App screen")).toBe("true");
    expect(within(group("Phone image")).queryByRole("button", { name: /rainfall/ })).toBeNull();
    press("Phone image", "notes.png");
    expect(shotOf("s1")?.screen).toEqual({ asset: NOTES.id });

    uploadAssets.mockImplementation(async () => {
      brandAssets = [...brandAssets, EVENTS];
      return { assets: [EVENTS], deduped: 0 };
    });
    const file = new File(["x"], "courtyard.jpg", { type: "image/jpeg" });
    fireEvent.change(screen.getByLabelText("Import a phone image"), { target: { files: [file] } });
    await waitFor(() => expect(shotOf("s1")?.screen).toEqual({ asset: EVENTS.id }));
    expect(uploadAssets).toHaveBeenCalledWith("brand", [file]);
    await waitFor(() => expect(pressed("Phone image", "courtyard.jpg")).toBe("true"));
    press("Phone image", "App screen");
    expect(shotOf("s1")).toEqual({ mode: "animation", layout: "screen" });
  });

  it("fills up to three phones slot by slot, clears phones 2 and 3, and clearing phone 1 moves phone 2 to the front", async () => {
    brandAssets = [NOTES, PROMO, EVENTS, LOCK];
    await openSheet("s1");
    press("Layout", "Phone screen");
    await waitFor(() => expect(within(group("Phone image")).getAllByRole("button")).toHaveLength(4));
    const slot = (name: string) => within(group("Phones")).getByRole("button", { name }) as HTMLButtonElement;
    expect(slot("Phone 1 (front)").getAttribute("aria-pressed")).toBe("true");
    expect([slot("Phone 2").disabled, slot("Phone 3").disabled]).toEqual([true, true]);
    const before = history();
    press("Phone image", "notes.png");
    expect(slot("Phone 2").disabled).toBe(false);
    fireEvent.click(slot("Phone 2"));
    expect(slot("Phone 2").getAttribute("aria-pressed")).toBe("true");
    expect(within(group("Phone image")).queryByRole("button", { name: "App screen" })).toBeNull();
    press("Phone image", "courtyard.jpg");
    fireEvent.click(slot("Phone 3"));
    press("Phone image", "floor-map.png");
    expect(shotOf("s1")?.screen).toEqual({ asset: NOTES.id, more: [EVENTS.id, LOCK.id] });
    expect(history()).toBe(before + 3);
    expect(pressed("Phone image", "floor-map.png")).toBe("true");
    press("Phone image", "notes.png");
    expect(shotOf("s1")?.screen, "a picked slot changes").toEqual({ asset: NOTES.id, more: [EVENTS.id, NOTES.id] });
    act(() => undo());
    expect(shotOf("s1")?.screen).toEqual({ asset: NOTES.id, more: [EVENTS.id, LOCK.id] });

    fireEvent.click(within(group("Phones")).getByRole("button", { name: "Clear phone 2" }));
    expect(shotOf("s1")?.screen, "phone 3 moves up").toEqual({ asset: NOTES.id, more: [LOCK.id] });
    fireEvent.click(within(group("Phones")).getByRole("button", { name: "Clear phone 1" }));
    expect(shotOf("s1")?.screen, "phone 2 moves to the front").toEqual({ asset: LOCK.id });
    expect(within(group("Phones")).queryByRole("button", { name: /^Clear phone/ })).toBeNull();
    expect(slot("Phone 2").getAttribute("aria-pressed"), "the open slot stays picked").toBe("true");
    expect(slot("Phone 3").disabled).toBe(true);
    fireEvent.click(slot("Phone 1 (front)"));
    press("Phone image", "App screen");
    expect(shotOf("s1")).toEqual({ mode: "animation", layout: "screen" });
    expect(screen.getByText(/Fits 1–3 phone frames, each with a library image/)).toBeTruthy();
  });

  it("says so when an import is not an image", async () => {
    await openSheet("s1");
    press("Layout", "Phone screen");
    uploadAssets.mockResolvedValue({ assets: [PROMO], deduped: 0 });
    fireEvent.change(screen.getByLabelText("Import a phone image"), { target: { files: [new File(["x"], "rainfall.mp4")] } });
    expect((await screen.findByRole("alert")).textContent).toBe("That file is not an image.");
    expect(shotOf("s1")).toEqual({ mode: "animation", layout: "screen" });
  });
});

describe("screen images in the player", () => {
  function Probe({ project, out }: { project: Project; out: { specs: ReturnType<typeof animatedSceneSpecs> } }) {
    const screens = useScreenAssets(project);
    const media = speechMedia(project);
    out.specs = animatedSceneSpecs(project, WORDS, sceneSpans(project, media, WORDS), media, NO_CAST, screens);
    return null;
  }

  it("fetches the libraries only once a scene names an image, and resolves it to the library file's URL", async () => {
    const out = { specs: [] as ReturnType<typeof animatedSceneSpecs> };
    const { rerender } = render(<Probe project={PROJECT} out={out} />);
    expect(workspace).not.toHaveBeenCalled();
    const staged: Project = { ...PROJECT, workspaceId: "garden-1", scenes: [{ ...PROJECT.scenes![0], shot: { mode: "animation", layout: "screen", screen: { asset: NOTES.id } } }, ...PROJECT.scenes!.slice(1)] };
    rerender(<Probe project={staged} out={out} />);
    await waitFor(() => expect(out.specs[0].shot.screen?.image).toBe(`/media/workspace/brand/${NOTES.file}`));
    expect(workspace.mock.calls.map(([id]) => id)).toEqual(["garden-1", "brand"]);
    brandAssets = [NOTES, EVENTS];
    const three: Project = { ...staged, scenes: [{ ...staged.scenes![0], shot: { mode: "animation", layout: "screen", screen: { asset: NOTES.id, more: [EVENTS.id, LOCK.id] } } }, ...staged.scenes!.slice(1)] };
    act(() => refreshLibraries());
    rerender(<Probe project={three} out={out} />);
    await waitFor(() => expect(out.specs[0].shot.screen?.more?.[0]?.image).toBe(`/media/workspace/brand/${EVENTS.file}`));
    expect(out.specs[0].shot.screen?.more).toEqual([{ asset: EVENTS.id, image: `/media/workspace/brand/${EVENTS.file}` }, { asset: LOCK.id }]);
  });
});
