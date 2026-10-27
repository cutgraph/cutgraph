// @vitest-environment jsdom
import { CHARACTERS as testRegistry } from "motion-core";
import { SYNTHETIC_RIG as SAMPLE_RIG } from "../../motion-core/test/synthetic-rig.ts";
const previousRig = testRegistry[SAMPLE_RIG.id];
Object.assign(testRegistry, { [SAMPLE_RIG.id]: SAMPLE_RIG });
afterAll(() => {
  if (previousRig) Object.assign(testRegistry, { [SAMPLE_RIG.id]: previousRig });
  else Reflect.deleteProperty(testRegistry, SAMPLE_RIG.id);
});
// Characters and their custom expressions everywhere a scene is directed: the Shot view offers the
// video's characters when it can cast more than one, its expression picker lists the character's
// custom expressions after the built-ins, a pick saves the ids, the player specs carry the character's
// rig and custom spec, and a deleted expression (or another character's) falls back to the role's look.
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { LENS_EXPRESSIONS, resolveShot, type PartSpec } from "motion-core";
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
import { animatedSceneSpecs } from "../src/v3/story/shot/specs";
import { sceneSpans, speechMedia } from "../src/v3/story/model";
import { openShot, resetStory } from "../src/v3/story/storyStore";
import { undo, useProjectStore } from "../src/store/useProjectStore";
import type { CastExpression, CharacterProfile } from "../src/v3/api";
import type { Cast } from "../src/v3/library/cast";
import { resetCast, scopeKey, useCastStore } from "../src/v3/library/castStore";

const SMUG: PartSpec = { eyes: "side", mouth: "smile" };
const EXPRESSIONS_LIST: CastExpression[] = [
  { id: "smug", name: "Smug", spec: SMUG, createdAt: "t1" },
  { id: "sleepy-2", name: "Dozing", spec: LENS_EXPRESSIONS.sleepy, createdAt: "t2" },
];
const profile = (id: string, name: string, workspaceId: string, expressions: CastExpression[]): CharacterProfile => ({
  id, name, rig: "lens", art: [], palette: "periwinkle", approved: true, expressions, workspaceId, createdAt: "t",
});
const DEFAULT_PROFILES = (expressions = EXPRESSIONS_LIST) => profile("default-character", "Lens", "brand", expressions);
const GUEST = profile("notes-sample", "Notes Sample", "garden-1", [{ id: "grin", name: "Grin", spec: LENS_EXPRESSIONS.excited, createdAt: "t3" }]);

const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "cast-demo", revision: 3, schemaVersion: 3,
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "c", media: { source: "data/source/talk.mp4" }, srcInF: 0, srcOutF: 210, tlStartF: 0 }] }],
  scenes: [
    { id: "s1", title: "Open", role: "Hook", startWord: 0, shot: { mode: "animation" } },
    { id: "s2", title: "Middle", role: "Beat", startWord: 2, shot: { mode: "cameo", expression: "smug" } },
    { id: "s3", title: "Close", role: "Wrap", startWord: 4, shot: { mode: "cameo", expression: "deleted-one" } },
  ],
};
const IN_GARDEN: Project = { ...PROJECT, workspaceId: "garden-1" };

const shotOf = (id: string) => useProjectStore.getState().project?.scenes?.find((s) => s.id === id)?.shot;
const group = (name: string) => screen.getByRole("group", { name });
const names = (name: string) => within(group(name)).getAllByRole("button").map((b) => b.textContent);
const pressed = (row: string, name: string) => within(group(row)).getByRole("button", { name }).getAttribute("aria-pressed");

function setCast(cast: Cast, workspace: string | null = null): void {
  useCastStore.setState({ scopes: { [scopeKey({ workspace })]: { cast, status: "ready", error: null } } });
}
const brandCast = (expressions = EXPRESSIONS_LIST): Cast => ({ characters: [DEFAULT_PROFILES(expressions)], default: "default-character", workspace: null });
const gardenCast: Cast = { characters: [DEFAULT_PROFILES(), GUEST], default: "default-character", workspace: { id: "garden-1", name: "Garden" } };

function useProject(project: Project): void {
  useProjectStore.setState({ project, revision: 3, playheadF: 0, lastWarnings: [] });
  useProjectStore.temporal.getState().clear();
}

beforeEach(() => {
  resetStory();
  resetCast();
  setCast(brandCast());
  let revision = 3;
  saveProject.mockReset().mockImplementation(async () => ({ ok: true, revision: ++revision }));
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  useProject(PROJECT);
});
afterEach(cleanup);

async function openSheet(sceneId: string): Promise<void> {
  act(() => openShot(sceneId));
  render(<ShotSheet />);
  await screen.findByRole("dialog", { name: "Shot" });
}

describe("the Shot view's expression picker", () => {
  it("lists the custom expressions after the built-ins", async () => {
    await openSheet("s1");
    expect(names("Expression")).toEqual([
      "Neutral", "Happy", "Excited", "Surprised", "Curious", "Focused", "Sleepy", "Idea", "Confused", "Determined", "Proud", "Smug", "Dozing",
    ]);
    expect(screen.queryByRole("group", { name: "Character" })).toBeNull();
  });

  it("saves a picked custom expression by id, as one undo step", async () => {
    await openSheet("s1");
    fireEvent.click(within(group("Expression")).getByRole("button", { name: "Smug" }));
    expect(shotOf("s1")).toEqual({ mode: "animation", expression: "smug" });
    expect(pressed("Expression", "Smug")).toBe("true");
    expect(useProjectStore.temporal.getState().pastStates.length).toBe(1);
    act(() => undo());
    expect(shotOf("s1")).toEqual({ mode: "animation" });
  });

  it("shows a deleted expression as the role's look it falls back to", async () => {
    await openSheet("s3");
    expect(pressed("Expression", "Happy")).toBe("true");
    expect(within(group("Expression")).queryByRole("button", { name: "deleted-one" })).toBeNull();
  });

  it("follows the cast as it changes", async () => {
    await openSheet("s2");
    expect(pressed("Expression", "Smug")).toBe("true");
    act(() => setCast(brandCast(EXPRESSIONS_LIST.slice(1))));
    expect(within(group("Expression")).queryByRole("button", { name: "Smug" })).toBeNull();
    expect(pressed("Expression", "Neutral")).toBe("true");
  });
});

describe("the Shot view's character picker", () => {
  beforeEach(() => {
    useProject(IN_GARDEN);
    setCast(gardenCast, "garden-1");
  });

  it("offers the video's characters, the default picked", async () => {
    await openSheet("s1");
    expect(names("Character")).toEqual(["Lens", "Notes Sample"]);
    expect(pressed("Character", "Lens")).toBe("true");
    const order = [...screen.getByRole("dialog", { name: "Shot" }).querySelectorAll("[data-picker]")].map((el) => el.getAttribute("data-picker"));
    expect(order.slice(0, 3)).toEqual(["character", "expression", "pose"]);
  });

  it("keeps an unavailable character when another shot field is edited and saved", async () => {
    const stored = { ...GUEST, rig: "unregistered-rig", available: false };
    setCast({ ...gardenCast, characters: [DEFAULT_PROFILES(), stored] }, "garden-1");
    useProject({ ...IN_GARDEN, scenes: [{ ...PROJECT.scenes![0], shot: { mode: "animation", character: stored.id, pose: "stored-pose" } }, ...PROJECT.scenes!.slice(1)] });
    await openSheet("s1");
    expect(within(group("Character")).getByRole("button", { name: "Notes Sample · Unavailable" })).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Headline" }), { target: { value: "Edited headline" } });
    fireEvent.blur(screen.getByRole("textbox", { name: "Headline" }));
    expect(shotOf("s1")).toEqual({ mode: "animation", character: stored.id, pose: "stored-pose", headline: "Edited headline" });
    await waitFor(() => expect(saveProject).toHaveBeenCalledWith(PROJECT.name, expect.objectContaining({ scenes: expect.arrayContaining([expect.objectContaining({ id: "s1", shot: { mode: "animation", character: stored.id, pose: "stored-pose", headline: "Edited headline" } })]) }), 3));
    expect(useCastStore.getState().scopes[scopeKey({ workspace: "garden-1" })]?.cast.characters[1].rig).toBe(stored.rig);
  });

  it("casts a character as one undo step and shows its own expressions", async () => {
    await openSheet("s1");
    fireEvent.click(within(group("Character")).getByRole("button", { name: "Notes Sample" }));
    expect(shotOf("s1")).toEqual({ mode: "animation", character: "notes-sample" });
    expect(pressed("Character", "Notes Sample")).toBe("true");
    expect(names("Expression").slice(-1)).toEqual(["Grin"]);
    expect(within(group("Expression")).queryByRole("button", { name: "Smug" })).toBeNull();
    fireEvent.click(within(group("Expression")).getByRole("button", { name: "Grin" }));
    expect(shotOf("s1")).toEqual({ mode: "animation", character: "notes-sample", expression: "grin" });
    expect(useProjectStore.temporal.getState().pastStates.length).toBe(2);
  });

  it("drops a custom expression the new character doesn't have, and keeps a built-in", async () => {
    await openSheet("s2");
    fireEvent.click(within(group("Character")).getByRole("button", { name: "Notes Sample" }));
    expect(shotOf("s2")).toEqual({ mode: "cameo", character: "notes-sample" });
    fireEvent.click(within(group("Expression")).getByRole("button", { name: "Idea" }));
    fireEvent.click(within(group("Character")).getByRole("button", { name: "Lens" }));
    expect(shotOf("s2")).toEqual({ mode: "cameo", character: "default-character", expression: "idea" });
  });

  it("never lists a character from another project", async () => {
    useProject(PROJECT);
    setCast(brandCast());
    await openSheet("s1");
    expect(screen.queryByRole("group", { name: "Character" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Notes Sample" })).toBeNull();
  });
});

describe("the Sample in the Shot view", () => {
  const TEAL: CharacterProfile = { ...profile("sample-performer", "Fern Sample", "garden-1", []), rig: "sample-rig", palette: "fern", approved: false };
  const withFern = (approved: boolean): Cast => ({ ...gardenCast, characters: [...gardenCast.characters, { ...TEAL, approved }] });
  beforeEach(() => {
    useProject({ ...IN_GARDEN, scenes: IN_GARDEN.scenes!.map((s) => (s.id === "s1" ? { ...s, shot: { mode: "animation", character: "sample-performer" } } : s)) });
    setCast(withFern(false), "garden-1");
  });

  it("offers the Sample's own expressions, poses and moves, starting from its rest pose", async () => {
    await openSheet("s1");
    expect(names("Expression")).toEqual(["Neutral", "Happy", "Curious"]);
    expect(names("Pose")).toEqual(["Rest", "Wave", "Point"]);
    expect(names("Move")).toEqual(["Idle", "Nod", "Wave"]);
    expect(pressed("Pose", "Rest")).toBe("true");
    fireEvent.click(within(group("Pose")).getByRole("button", { name: "Wave" }));
    fireEvent.click(within(group("Expression")).getByRole("button", { name: "Happy" }));
    expect(shotOf("s1")).toEqual({ mode: "animation", character: "sample-performer", pose: "wave", expression: "happy" });
  });

  it("marks the shot as a draft until the Sample's look is approved", async () => {
    await openSheet("s1");
    expect(screen.getByText("Draft").getAttribute("title")).toMatch(/^Fern Sample: a draft look/);
    act(() => setCast(withFern(true), "garden-1"));
    expect(screen.queryByText("Draft")).toBeNull();
    act(() => setCast(withFern(false), "garden-1"));
    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Footage" }));
    expect(screen.queryByText("Draft")).toBeNull();
  });
});

describe("a draft co-star", () => {
  it("marks a duo whose co-star's look is not approved yet", async () => {
    const red: CharacterProfile = { ...profile("sample-performer", "Fern Sample", "garden-1", []), rig: "sample-rig", palette: "fern", approved: false };
    useProject({ ...IN_GARDEN, scenes: IN_GARDEN.scenes!.map((s) => (s.id === "s1"
      ? { ...s, shot: { mode: "animation", costar: { character: "sample-performer" }, interaction: "fistbump" } } : s)) });
    setCast({ ...gardenCast, characters: [...gardenCast.characters, red] }, "garden-1");
    await openSheet("s1");
    expect(screen.getByText("Draft").getAttribute("title")).toMatch(/^Fern Sample: a draft look/);
  });
});

describe("characters and custom expressions in the Story", () => {
  const specs = (project: Project, cast: Cast) => {
    const media = speechMedia(project);
    return animatedSceneSpecs(project, WORDS, sceneSpans(project, media, WORDS), media, cast);
  };

  it("names them on the scene's shot chip, with the character once there is a choice", () => {
    render(<>{PROJECT.scenes!.map((s) => <ShotChip key={s.id} scene={s} />)}</>);
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["✦ Confused", "✦ Smug", "✦ Happy"]);
    cleanup();
    const cast = { ...IN_GARDEN, scenes: [{ ...PROJECT.scenes![0], shot: { mode: "cameo" as const, character: "notes-sample", expression: "grin" } }] };
    useProject(cast);
    setCast(gardenCast, "garden-1");
    render(<ShotChip scene={cast.scenes[0]} />);
    expect(screen.getByRole("button").textContent).toBe("✦ Grin · Notes Sample");
  });

  it("gives the player the custom spec, and the role's look for a deleted one", () => {
    const got = specs(PROJECT, brandCast());
    expect(got.map((s) => [s.sceneId, s.shot.expression, s.shot.custom])).toEqual([
      ["s1", "confused", undefined],
      ["s2", "smug", SMUG],
      ["s3", "happy", undefined],
    ]);
    expect(got[2].shot).toEqual(resolveShot({ mode: "cameo" }, "Wrap", "Close"));
  });

  it("gives the player the character's rig and its own custom spec", () => {
    const project = { ...IN_GARDEN, scenes: [{ ...PROJECT.scenes![0], shot: { mode: "animation" as const, character: "notes-sample", expression: "grin" } }] };
    const [spec] = specs(project, gardenCast);
    expect([spec.shot.character, spec.shot.palette, spec.shot.expression, spec.shot.custom]).toEqual(["lens", "periwinkle", "grin", LENS_EXPRESSIONS.excited]);
    const [outside] = specs({ ...project, workspaceId: undefined }, brandCast());
    expect([outside.shot.expression, outside.shot.custom]).toEqual(["confused", undefined]);
  });
});
