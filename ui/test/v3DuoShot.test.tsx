import { characterOf as defaultRigOf, DEFAULT_CHARACTER as defaultRigId } from "motion-core";
const DEFAULT_RIG = defaultRigOf(defaultRigId);
// @vitest-environment jsdom
// Duo shots: the Shot view adds a co-star when the video can cast two characters, with its own
// character, expression, pose and move pickers and an interaction; each pick is one undo step and
// Remove takes the pair apart in one. Chips read both expressions, the player spec carries the
// co-star resolved against the cast, and motion-core lays the pair out per format, mirrors the co-star
// to face the lead, lands the interaction on the cue word's frame and draws deterministically.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  LENS_EXPRESSIONS, FORMATS, INTERACTIONS, drawSceneFrame, duoBeat, duoLayout,
  type Ctx, type Format, type MotionEnv, type Shot as MotionShot,
} from "motion-core";
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
import type { CharacterProfile } from "../src/v3/api";
import { castShot, type Cast } from "../src/v3/library/cast";
import { resetCast, scopeKey, useCastStore } from "../src/v3/library/castStore";

const profile = (id: string, name: string, workspaceId: string, expressions: CharacterProfile["expressions"] = []): CharacterProfile => ({
  approved: true,
  id, name, rig: "lens", art: [], palette: "periwinkle", expressions, workspaceId, createdAt: "t",
});
const DEFAULT_PROFILES = profile("default-character", "Lens", "brand");
const GUEST = profile("notes-guest", "Notes Guest", "garden-1", [{ id: "grin", name: "Grin", spec: LENS_EXPRESSIONS.excited, createdAt: "t3" }]);
const DUO_CAST: Cast = { characters: [DEFAULT_PROFILES, GUEST], default: "default-character", workspace: { id: "garden-1", name: "Garden" } };
const SOLO_CAST: Cast = { characters: [DEFAULT_PROFILES], default: "default-character", workspace: null };

const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "duo-demo", revision: 3, schemaVersion: 3, workspaceId: "garden-1",
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "c", media: { source: "data/source/talk.mp4" }, srcInF: 0, srcOutF: 210, tlStartF: 0 }] }],
  scenes: [
    { id: "s1", title: "Open", role: "Hook", startWord: 0, shot: { mode: "animation", cueWord: 1 } },
    { id: "s2", title: "Middle", role: "Beat", startWord: 2, shot: { mode: "cameo" } },
    { id: "s3", title: "Close", role: "Wrap", startWord: 4 },
  ],
};

const shotOf = (id: string) => useProjectStore.getState().project?.scenes?.find((s) => s.id === id)?.shot;
const history = () => useProjectStore.temporal.getState().pastStates.length;
const group = (name: string) => screen.getByRole("group", { name });
const pressed = (row: string, name: string) => within(group(row)).getByRole("button", { name }).getAttribute("aria-pressed");

function setCast(cast: Cast): void {
  useCastStore.setState({ scopes: { [scopeKey({ workspace: "garden-1" })]: { cast, status: "ready", error: null } } });
}

function useProject(project: Project): void {
  useProjectStore.setState({ project, revision: 3, playheadF: 0, lastWarnings: [] });
  useProjectStore.temporal.getState().clear();
}

beforeEach(() => {
  resetStory();
  resetCast();
  setCast(DUO_CAST);
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

describe("Shot view: co-star", () => {
  it("offers Add co-star on an animation only when the video can cast two characters", async () => {
    setCast(SOLO_CAST);
    await openSheet("s1");
    expect(screen.queryByRole("button", { name: "+ Add co-star" })).toBeNull();
    cleanup();
    setCast(DUO_CAST);
    await openSheet("s2");
    expect(screen.queryByRole("button", { name: "+ Add co-star" })).toBeNull();
    cleanup();
    await openSheet("s1");
    expect(screen.getByRole("button", { name: "+ Add co-star" })).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Interaction" })).toBeNull();
  });

  it("points the way to a duo from Footage, and animates with a co-star in one step", async () => {
    setCast(SOLO_CAST);
    await openSheet("s3");
    expect(screen.queryByRole("button", { name: "Animate with a co-star" })).toBeNull();
    cleanup();
    setCast(DUO_CAST);
    await openSheet("s3");
    expect(screen.getByText("Characters and “+ Add co-star” come with Animation: Lens and Notes Guest can share the scene.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Animate with a co-star" }));
    expect(shotOf("s3")).toEqual({ mode: "animation", costar: { character: "notes-guest" } });
    expect(history()).toBe(1);
    expect(pressed("Co-star", "Notes Guest")).toBe("true");
    expect(pressed("Interaction", "Side by side")).toBe("true");
    expect(screen.queryByRole("button", { name: "Animate with a co-star" })).toBeNull();
  });

  it("puts + Add co-star in the default rig row, up top with the lead", async () => {
    await openSheet("s1");
    const row = screen.getByRole("group", { name: "Character" }).parentElement!;
    expect(within(row).getByRole("button", { name: "+ Add co-star" })).toBeTruthy();
  });

  it("adds the other character as co-star, then directs it and the interaction one undo step at a time", async () => {
    await openSheet("s1");
    fireEvent.click(screen.getByRole("button", { name: "+ Add co-star" }));
    expect(shotOf("s1")).toEqual({ mode: "animation", cueWord: 1, costar: { character: "notes-guest" } });
    expect(history()).toBe(1);
    expect(pressed("Co-star", "Notes Guest")).toBe("true");
    expect(within(group("Co-star expression")).getAllByRole("button").map((b) => b.textContent)).toContain("Grin");
    expect(pressed("Co-star expression", "Happy")).toBe("true");
    expect(pressed("Co-star pose", "Rest")).toBe("true");
    expect(pressed("Interaction", "Side by side")).toBe("true");

    const picks: [string, string, Record<string, unknown>][] = [
      ["Co-star expression", "Grin", { costar: { character: "notes-guest", expression: "grin" } }],
      ["Co-star pose", "Point", { costar: { character: "notes-guest", expression: "grin", pose: "point" } }],
      ["Co-star move", "Hop", { costar: { character: "notes-guest", expression: "grin", pose: "point", move: "hop" } }],
      ["Interaction", "Fist bump", { interaction: "fistbump" }],
    ];
    let expected: Record<string, unknown> = { ...shotOf("s1") };
    for (const [row, name, patch] of picks) {
      fireEvent.click(within(group(row)).getByRole("button", { name }));
      expected = { ...expected, ...patch };
      expect(shotOf("s1")).toEqual(expected);
      expect(pressed(row, name)).toBe("true");
    }
    expect(history()).toBe(1 + picks.length);
    fireEvent.click(within(group("Co-star pose")).getByRole("button", { name: "Point" }));
    expect(history()).toBe(1 + picks.length);

    fireEvent.click(within(group("Co-star")).getByRole("button", { name: "Lens" }));
    expect(shotOf("s1")?.costar).toEqual({ character: "default-character", pose: "point", move: "hop" });
    fireEvent.click(within(group("Interaction")).getByRole("button", { name: "Side by side" }));
    expect(shotOf("s1")).not.toHaveProperty("interaction");
  });

  it("removes the co-star and its interaction as one step, and undo brings the pair back", async () => {
    useProject({ ...PROJECT, scenes: [{ ...PROJECT.scenes![0], shot: { mode: "animation", costar: { character: "notes-guest" }, interaction: "highfive" } }, ...PROJECT.scenes!.slice(1)] });
    await openSheet("s1");
    fireEvent.click(screen.getByRole("button", { name: "Remove co-star" }));
    expect(shotOf("s1")).toEqual({ mode: "animation" });
    expect(history()).toBe(1);
    expect(screen.getByRole("button", { name: "+ Add co-star" })).toBeTruthy();
    act(() => undo());
    expect(shotOf("s1")).toEqual({ mode: "animation", costar: { character: "notes-guest" }, interaction: "highfive" });
  });
});

describe("duo chips and player specs", () => {
  const duoProject: Project = {
    ...PROJECT,
    scenes: [
      { ...PROJECT.scenes![0], shot: { mode: "animation", cueWord: 1, costar: { character: "notes-guest", expression: "grin", pose: "point" }, interaction: "fistbump" } },
      { ...PROJECT.scenes![1], shot: { mode: "cameo", costar: { character: "notes-guest" } } },
      PROJECT.scenes![2],
    ],
  };

  it("chips read both expressions, lead first; a cameo never shows the co-star", () => {
    render(<><ShotChip scene={duoProject.scenes![0]} /><ShotChip scene={duoProject.scenes![1]} /></>);
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["✦ Confused + Grin", "✦ Neutral · Lens"]);
  });

  it("the player spec carries the co-star resolved against the cast, and the beat on the cue word", () => {
    const media = speechMedia(duoProject);
    const [spec] = animatedSceneSpecs(duoProject, WORDS, sceneSpans(duoProject, media, WORDS), media, DUO_CAST);
    expect(spec.shot.costar).toEqual({ character: "lens", palette: "periwinkle", expression: "grin", pose: "point", move: "idle", custom: LENS_EXPRESSIONS.excited });
    expect(spec.shot.interaction).toBe("fistbump");
    expect(Math.round(spec.timing.cueT! * 30)).toBe(33);
    const f = (frame: number) => duoBeat("fistbump", (frame - spec.startF) / 30, spec.timing.cueT!);
    expect(f(32).lead.approach).toBeLessThan(1);
    expect([f(33).lead.approach, f(33).burst]).toEqual([1, 0]);
    expect(f(34).lead.squash).toBeGreaterThan(0);
    expect(f(36).lead.approach).toBeLessThan(1);
  });
});

// A 2D context that records every call and property write, so drawing can be compared without pixels.
function recorder(): { ctx: Ctx; env: MotionEnv; log: unknown[] } {
  const log: unknown[] = [];
  const make = (tag: string): Ctx => new Proxy({}, {
    get: (target, key) => {
      if (Reflect.has(target, key)) return Reflect.get(target, key);
      if (key === "canvas") return { width: 1920, height: 1080 };
      if (key === "measureText") return (s: string) => ({ width: s.length * 10 });
      // A recorder keeps no transform: it reports the identity, so the rig draws as unmirrored.
      if (key === "getTransform") return () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 });
      if (key === "createRadialGradient") return (...a: unknown[]) => (log.push([tag, key, ...a]), { addColorStop: (...b: unknown[]) => log.push([tag, "stop", ...b]) });
      return (...a: unknown[]) => { log.push([tag, key, ...a]); };
    },
    set: (target, key, value) => (log.push([tag, "=", key, value]), Reflect.set(target, key, value)),
  }) as Ctx;
  return { ctx: make("frame"), env: { createCanvas: () => ({ getContext: () => make("rig") }) as never }, log };
}

const duoShot = (interaction: string): MotionShot => ({
  character: "lens", palette: "periwinkle", pose: "rest", expression: "happy", move: "idle", backdrop: "route", diagram: "route", headline: "Launch",
  costar: { character: "lens", palette: "periwinkle", pose: "rest", expression: "idea", move: "idle" }, interaction: interaction as MotionShot["interaction"],
});

// The calls as data: a canvas argument (the rig's offscreen canvas) serializes without its methods.
function draw(shot: MotionShot, lt: number): unknown[] {
  const { ctx, env, log } = recorder();
  drawSceneFrame(ctx, "animation", shot, lt, { firstT: 0.4, cueT: 2 }, 1920, 1080, env);
  return JSON.parse(JSON.stringify(log)) as unknown[];
}

describe("motion-core duo layout, facing, timing and determinism", () => {
  it("lays the pair out per format: lead left, co-star right, feet level", () => {
    for (const format of Object.keys(FORMATS) as Format[]) {
      const [W, H] = FORMATS[format];
      const L = duoLayout(W, H, DEFAULT_RIG, DEFAULT_RIG, true);
      expect(L.lead.x).toBeLessThan(W / 2);
      expect(L.costar.x).toBeGreaterThan(W / 2);
      expect(L.lead.by).toBe(L.costar.by);
      expect(L.costar.x - L.lead.x).toBeGreaterThan(0.53 * L.lead.h);
    }
    expect(duoLayout(1920, 1080, DEFAULT_RIG, DEFAULT_RIG, true).lead.x).toBe(640);
  });

  it("mirrors the co-star to face the lead only when they interact", () => {
    const mirrored = (log: unknown[]) => log.some((e) => Array.isArray(e) && e[0] === "frame" && e[1] === "scale" && (e[2] as number) < 0);
    expect(mirrored(draw(duoShot("none"), 1))).toBe(false);
    for (const kind of INTERACTIONS.filter((k) => k !== "none")) expect(mirrored(draw(duoShot(kind), 1))).toBe(true);
  });

  it("lands every contact interaction on the beat, and is a pure function of time", () => {
    for (const kind of ["fistbump", "highfive"] as const) {
      expect(duoBeat(kind, 2, 2).lead.approach).toBe(1);
      expect(duoBeat(kind, 1.9, 2).lead.approach).toBeLessThan(1);
      expect(duoBeat(kind, 2.5, 2).burst).toBeNull();
      expect(duoBeat(kind, 2.1, 2)).toEqual(duoBeat(kind, 2.1, 2));
    }
    expect(duoBeat("point", 2.5, 2).pointer?.p).toBeGreaterThan(0);
    expect(duoBeat("point", 1.9, 2).pointer).toBeNull();
  });

  // Where each performer's hand is drawn: the frame's translate to its slot and its squash/mirror scale,
  // then its rig's own translate (a hop lifts it) and the drawImage whose height gives the rig's pixel
  // scale. The front of the hand is its anchor (rig data) through those transforms.
  function hands(shot: MotionShot, lt: number): { x: number; y: number; g: number }[] {
    const { ctx, env, log } = recorder();
    drawSceneFrame(ctx, "animation", shot, lt, { firstT: 0.4, cueT: 2 }, 1920, 1080, env);
    const frame = (log as unknown[][]).filter((e) => e[0] === "frame");
    const last = (key: string, before: number) => frame.slice(0, before).map((e, i) => [e, i] as const).filter(([e]) => e[1] === key).pop()!;
    return frame.flatMap((e, i) => {
      if (e[1] !== "drawImage") return [];
      const [rigT, r] = last("translate", i), [slotT, s] = last("translate", r), [scale] = last("scale", r);
      const h = e[6] as number, rig = DEFAULT_RIG, g = h / rig.rows!;
      const [fwd, up] = rig.hands![shot.interaction === "highfive" ? "wave" : "point"];
      expect(s).toBeLessThan(r);
      return [{ x: (slotT[2] as number) + (scale[2] as number) * fwd * g, y: (slotT[3] as number) + (scale[3] as number) * ((rigT[3] as number) - up * g), g }];
    });
  }

  it("meets the characters' hands on the cue frame", () => {
    const guest: CharacterProfile = { ...profile("prep-cook", "Notes Guest", "garden-1"), rig: "lens", palette: "periwinkle", approved: true };
    const cast: Cast = { ...DUO_CAST, characters: [DEFAULT_PROFILES, guest] };
    for (const interaction of ["highfive", "fistbump"] as const) {
      const shot = castShot({ mode: "animation", costar: { character: "prep-cook" }, interaction }, "Payoff", "Launch", cast);
      expect([shot.character, shot.costar?.character, shot.costar?.palette]).toEqual(["lens", "lens", "periwinkle"]);
      const [costar, lead] = hands(shot, 2);
      expect(Math.abs(lead.x - costar.x)).toBeLessThanOrEqual(1);
      expect(Math.abs(lead.y - costar.y)).toBeLessThanOrEqual(lead.g);
      // Apart, long before the wind-up, with the default rig standing between the hops of its celebration.
      const [apartCostar, apartLead] = hands(shot, 0.8);
      expect(apartCostar.x - apartLead.x).toBeGreaterThan(200);
    }
  });

  it("draws the same commands for the same moment, and different ones as the pair moves", () => {
    for (const kind of INTERACTIONS) expect(draw(duoShot(kind), 2.05)).toEqual(draw(duoShot(kind), 2.05));
    expect(draw(duoShot("fistbump"), 2)).not.toEqual(draw(duoShot("fistbump"), 1.2));
  });
});
