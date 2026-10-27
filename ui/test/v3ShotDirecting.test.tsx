// @vitest-environment jsdom
// The Shot view's directing aids: numbered takes, the critic line, "Direct it", the scene's code
// and the stage captions, plus draft expressions kept out of the pickers.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { castShot, NO_CAST, DEFAULT_PROFILE } from "../src/v3/library/cast";
import type { ChatConfig, TranscriptWord } from "../src/api";
import type { Project, Shot } from "../src/types/project";

const WORDS: TranscriptWord[] = ["Rain", "falls", "outside.", "Read", "the", "map,", "then", "explore."].map((w, i) => ({ w, start: i + 0.1, end: i + 0.6, segIdx: 0 }));
const mocks = vi.hoisted(() => ({ saveProject: vi.fn(), sendChat: vi.fn(), acceptProposal: vi.fn() }));
vi.mock("../src/api", () => ({
  api: {
    transcription: async () => ({ state: "ready", job: null }),
    getTranscript: async () => ({ words: WORDS, segments: [], fillers: [] }),
    saveProject: mocks.saveProject,
    sendChat: mocks.sendChat,
    acceptProposal: mocks.acceptProposal,
  },
}));
vi.mock("../src/toast", () => ({ toast: () => {} }));

import ShotSheet from "../src/v3/story/shot/ShotSheet";
import { critic } from "../src/v3/story/shot/critic";
import { sceneCode } from "../src/v3/story/shot/code";
import { drawPillCaption } from "../src/v3/story/shot/stageCaptions";
import { resetTakes } from "../src/v3/story/shot/takes";
import { directContext, sceneShotCards } from "../src/v3/story/shot/DirectIt";
import { stripContext } from "../src/v3/story/ask/ask";
import { openShot, resetStory, useStory } from "../src/v3/story/storyStore";
import { undo, useProjectStore } from "../src/store/useProjectStore";
import { ingestAgentEvent, useChatStore } from "../src/store/useChatStore";
import { initialChatState, type ProposalCard } from "../src/chat/agentEvents";
import { resetCast, scopeKey, useCastStore } from "../src/v3/library/castStore";
import type { PillPage } from "../src/preview/captionPages";

const PROJECT: Project = {
  fps: 30, width: 1920, height: 1080, name: "shot-demo", revision: 3, schemaVersion: 3,
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "c", media: { source: "data/source/talk.mp4" }, srcInF: 0, srcOutF: 240, tlStartF: 0 }] }],
  scenes: [
    { id: "s1", title: "A rainy visit", role: "Hook", startWord: 0 },
    { id: "s2", title: "Gallery map", role: "Beat", startWord: 3, shot: { mode: "animation", expression: "happy" } },
  ],
};

const READY = {
  schemaVersion: 1,
  providers: [{
    id: "claude", label: "Claude", availability: { available: true, error: null },
    profiles: [{ id: "default", label: "Default", model: null, efforts: [], modes: ["default"],
      capabilities: { proposalOnly: true, planReadOnly: true, autoFullAccess: false }, availability: { available: true, error: null } }],
  }],
  selectionDefaults: { providerId: "claude", profileId: "default", model: null, effort: null, mode: "default" },
  models: [], efforts: [], modes: [], defaults: { model: null, effort: null, mode: "default" },
} as unknown as ChatConfig;

const shotOf = (id: string) => useProjectStore.getState().project?.scenes?.find((s) => s.id === id)?.shot;
const history = () => useProjectStore.temporal.getState().pastStates.length;
const group = (name: string) => screen.getByRole("group", { name });
const takes = () => within(group("Takes")).getAllByRole("button").map((b) => `${b.textContent}${b.getAttribute("aria-pressed") === "true" ? "*" : ""}`);

beforeEach(() => {
  resetStory();
  resetCast();
  resetTakes();
  let revision = 3;
  mocks.saveProject.mockReset().mockImplementation(async () => ({ ok: true, revision: ++revision }));
  mocks.sendChat.mockReset().mockResolvedValue({ ok: true });
  mocks.acceptProposal.mockReset();
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  useChatStore.setState({ ...initialChatState(), chatConfig: null });
  useProjectStore.setState({ project: PROJECT, revision: 3, playheadF: 0, lastWarnings: [] });
  useProjectStore.temporal.getState().clear();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openSheet(sceneId: string) {
  act(() => openShot(sceneId));
  const view = render(<ShotSheet />);
  await screen.findByRole("dialog", { name: "Shot" });
  // The opening look becomes take 1 in an effect; under load it can land after the dialog appears.
  await within(group("Takes")).findByRole("button", { name: "Take 1" });
  return view;
}

describe("Shot view takes", () => {
  it("numbers every committed change, puts a take back as one undo step, and undo returns", async () => {
    await openSheet("s1");
    expect(takes()).toEqual(["1*"]);
    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Animation" }));
    fireEvent.click(within(group("Expression")).getByRole("button", { name: "Excited" }));
    fireEvent.click(within(group("Pose")).getByRole("button", { name: "Cheer" }));
    expect(takes()).toEqual(["1", "2", "3", "4*"]);
    const third = shotOf("s1");
    expect(third).toEqual({ mode: "animation", expression: "excited", pose: "cheer" });

    const before = history();
    fireEvent.click(screen.getByRole("button", { name: "Take 1" }));
    expect(shotOf("s1")).toBeUndefined();
    expect(history()).toBe(before + 1);
    expect(takes()).toEqual(["1*", "2", "3", "4"]);

    fireEvent.click(screen.getByRole("button", { name: "Take 3" }));
    expect(shotOf("s1")).toEqual({ mode: "animation", expression: "excited" });
    act(() => undo());
    expect(shotOf("s1")).toBeUndefined();
    act(() => undo());
    expect(shotOf("s1")).toEqual(third);
    expect(takes()).toEqual(["1", "2", "3", "4*"]);
  });

  it("keeps a scene's takes for the session when the Shot view closes and opens again", async () => {
    const view = await openSheet("s2");
    fireEvent.click(within(group("Move")).getByRole("button", { name: "Wave" }));
    expect(takes()).toEqual(["1", "2*"]);
    act(() => openShot(null));
    view.unmount();
    await openSheet("s2");
    expect(takes()).toEqual(["1", "2*"]);
  });
});

describe("the critic", () => {
  const shot = (patch: Record<string, unknown>, role = "Hook") => castShot({ mode: "animation", ...patch }, role, "A title", NO_CAST);
  const run = (patch: Record<string, unknown>, role = "Hook", mode: "animation" | "cameo" | "footage" = "animation", layout?: string) =>
    critic({ mode, layout, role, shot: shot(patch, role), cueWord: "outside.", profile: DEFAULT_PROFILE });

  it("passes a sound shot, naming the word it lands on", () => {
    expect(run({ expression: "excited", pose: "cheer", move: "hop" })).toEqual({
      ok: true, notes: ["Lands on “outside”, text inside title-safe, Lens at whole-pixel scale."],
    });
  });

  it("flags a low-energy face on a Hook or Payoff, a move that sets the arms, and a long headline", () => {
    expect(run({ expression: "sleepy", pose: "cheer", move: "hop" })?.notes).toEqual(["Sleepy reads low-energy for a hook scene."]);
    expect(run({ expression: "focused", pose: "cheer", move: "hop" }, "Payoff")?.notes).toEqual(["Focused reads low-energy for a payoff scene."]);
    expect(run({ expression: "focused", pose: "cheer", move: "hop" }, "Problem")?.ok).toBe(true);
    expect(run({ expression: "happy", pose: "point", move: "wave" })?.notes).toEqual(["“Wave” sets the arms, so the point pose is overridden."]);
    expect(run({ expression: "happy", pose: "cheer", move: "celebrate" })?.ok).toBe(true);
    const long = run({ expression: "happy", pose: "cheer", move: "hop", headline: "A headline that runs on well past forty-two characters" });
    expect(long).toEqual({ ok: false, notes: ["The headline is long for this scene; aim for under 42 characters."] });
  });

  it("judges only what is drawn: no headline in a cameo, no performer on a title card, nothing for footage", () => {
    expect(run({ expression: "sleepy", pose: "point", move: "wave", headline: "x".repeat(50) }, "Hook", "cameo")?.notes).toEqual([
      "Sleepy reads low-energy for a hook scene.", "“Wave” sets the arms, so the point pose is overridden.",
    ]);
    expect(run({ expression: "sleepy", pose: "point", move: "wave" }, "Hook", "animation", "title")).toEqual({ ok: true, notes: ["Lands on “outside”, text inside title-safe."] });
    expect(run({}, "Hook", "footage")).toBeNull();
  });

  it("shows under the stage and reacts to each change", async () => {
    await openSheet("s1");
    expect(screen.queryByRole("status", { name: "Critic" })).toBeNull();
    fireEvent.click(within(group("Picture")).getByRole("button", { name: "Animation" }));
    fireEvent.click(within(group("Expression")).getByRole("button", { name: "Excited" }));
    fireEvent.click(within(group("Move")).getByRole("button", { name: "Hop" }));
    expect(screen.getByRole("status", { name: "Critic" }).textContent).toBe("✓ Critic Lands on “rain”, text inside title-safe, Lens at whole-pixel scale.");
    fireEvent.click(within(group("Expression")).getByRole("button", { name: "Sleepy" }));
    const line = screen.getByRole("status", { name: "Critic" });
    expect([line.className, line.textContent]).toEqual(["critic warn", "! Critic Sleepy reads low-energy for a hook scene."]);
  });
});

describe("View code", () => {
  const base = { n: 2, title: "Gallery map", role: "Beat", cueWord: "map,", durS: 3.42, captions: true, screenName: null };

  it("describes an animated scene as illustrative pseudocode, every look spelled out", () => {
    const stored = { mode: "animation" as const, costar: { character: "visitor-1", expression: "curious" }, interaction: "fistbump" as const, bubble: "think" as const, camera: "push-in" as const };
    const code = sceneCode({ ...base, mode: "animation", stored, shot: castShot(stored, "Beat", "Gallery map", NO_CAST) });
    expect(code).toBe([
      "// Illustrative pseudocode: these JSX tags and words helpers are not executable APIs.",
      "// scenes/scene-02.tsx · “Gallery map” · Beat · motion-core draws it in the preview and the export",
      "import {Scene, Backdrop, Diagram, Character, Costar, Headline, Camera, Captions} from '../studio';",
      "import words from '../words.json';",
      "",
      "const scene = words.scene(2);    // WhisperX timings · 3.4 s",
      "const cue = scene.word('map').start;    // “Lands on”: the beat lands here",
      "",
      "export const Scene02 = () => (",
      '  <Scene mode="animation">',
      '    <Backdrop kind="work" />',
      '    <Diagram kind="blank" at={cue} />',
      '    <Character pose="rest" expression="neutral" move="idle" bubble="think" />',
      '    <Costar character="visitor-1" pose="rest" expression="curious" move="idle" interaction="fistbump" />',
      '    <Headline>Gallery map</Headline>',
      '    <Camera move="push-in" />',
      "    <Captions words={scene.words} />",
      "  </Scene>",
      ");",
      "",
    ].join("\n"));
  });

  it("writes staged layouts, cameos and footage", () => {
    const title: Shot = { mode: "animation", layout: "title", badges: [{ text: "NEW", tone: "ink" }], headline: "Visit today" };
    expect(sceneCode({ ...base, mode: "animation", stored: title, shot: castShot(title, "Beat", "x", NO_CAST), captions: false })).toContain(
      '    <Backdrop kind="work" />\n    <TitleCard>\n      <Badge tone="ink">NEW</Badge>\n    </TitleCard>\n    <Headline>Visit today</Headline>\n  </Scene>');
    const phone = { mode: "animation" as const, layout: "screen" as const };
    expect(sceneCode({ ...base, mode: "animation", stored: phone, shot: castShot(phone, "Beat", "x", NO_CAST), screenName: "Courtyard" })).toContain('<Phone screen="Courtyard" />');
    const fan: Shot = { ...phone, screen: { asset: "a", more: ["b", "c"] } };
    const fanCode = sceneCode({ ...base, mode: "animation", stored: fan, shot: castShot(fan, "Beat", "x", NO_CAST), screenName: "Gallery", moreScreens: ["Floor map", "Entrance"] });
    expect(fanCode.match(/<Phone screen="[^"]+" \/>/g)).toEqual(['<Phone screen="Gallery" />', '<Phone screen="Floor map" />', '<Phone screen="Entrance" />']);
    const signoff: Shot = { mode: "animation", layout: "signoff" };
    const signoffCode = sceneCode({ ...base, mode: "animation", stored: signoff, shot: castShot(signoff, "Wrap", "x", NO_CAST) });
    expect(signoffCode).toContain('<SignOff />\n    <Character pose="rest" expression="neutral" move="idle" />');
    const cameo = sceneCode({ ...base, mode: "cameo", stored: { mode: "cameo" }, shot: castShot({ mode: "cameo" }, "Wrap", "x", NO_CAST) });
    expect(cameo).toContain('    <Footage />\n    <Character cameo pose="wave" expression="happy" move="wave" />\n');
    const footage = sceneCode({ ...base, mode: "footage", stored: undefined, shot: castShot(undefined, "Beat", "x", NO_CAST) });
    expect(footage).not.toContain("const cue");
    expect(footage).toContain('  <Scene mode="footage">\n    <Footage />\n    <Captions words={scene.words} />\n  </Scene>');
  });

  it("copies the code", async () => {
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    await openSheet("s2");
    fireEvent.click(screen.getByRole("button", { name: "View code" }));
    const code = screen.getByLabelText("Scene code").textContent;
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(code);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
  });
});

describe("Direct it", () => {
  it("explains an unavailable assistant and links to Add-ons", async () => {
    const config = structuredClone(READY);
    const unavailable = { available: false, error: { code: "assistant_not_installed", message: "Assistant isn't installed." } };
    config.providers[0].availability = unavailable;
    config.providers[0].profiles[0].availability = unavailable;
    useChatStore.setState({ chatConfig: config });
    await openSheet("s2");
    expect(screen.getByText("Assistant isn't installed.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Settings, Add-ons" }).getAttribute("href")).toContain("#addons");
    expect(screen.getByRole("textbox", { name: "Direct the shot" })).toHaveProperty("disabled", true);
  });
  const card = (id: string, sceneId: string, i: number): ProposalCard & { type: string } => ({
    type: "proposal", id, title: `Animate scene ${i + 1}`, rationale: "More excited", status: "pending", baseRevision: 3,
    ops: [{ op: "test", path: `/scenes/${i}/id`, value: sceneId }, { op: "add", path: `/scenes/${i}/shot`, value: { mode: "animation", expression: "excited" } }],
    marks: [{ a: 3, b: 8, kind: "mark", label: "Animate “Gallery map” · excited" }],
  });

  it("scopes a turn to one scene's shot in the Story context the Ask sheet strips, naming its picture", () => {
    expect(directContext(2, "s2", "Gallery [map]", "animation")).toBe("\n\n[Story view · Shot view of scene 2, s2 “Gallery [map ” (animation) · direct only this scene's shot, with set_shot]");
    expect(stripContext(`make the default rig wave${directContext(2, "s2", "Gallery map", "footage")}`)).toBe("make the default rig wave");
  });

  it("tells the co-pilot a footage scene shows no character until it is a cameo or an animation", () => {
    expect(directContext(1, "s1", "Map", "footage")).toBe(
      "\n\n[Story view · Shot view of scene 1, s1 “Map” (footage, which shows no character: set mode cameo or animation to show one) · direct only this scene's shot, with set_shot]",
    );
  });

  it("keeps the proposals that change this scene's shot", () => {
    const cards = [card("a", "s2", 1), card("b", "s1", 0), { ...card("c", "s2", 1), ops: [{ op: "test", path: "/scenes/1/id", value: "s2" }, { op: "replace", path: "/scenes/1/title", value: "x" }] }];
    expect(sceneShotCards(cards, "s2").map((c) => c.id)).toEqual(["a"]);
  });

  it("sends a direction as a scoped co-pilot turn and shows this scene's proposal card to accept", async () => {
    useChatStore.setState({ chatConfig: READY });
    await openSheet("s2");
    const input = screen.getByRole("textbox", { name: "Direct the shot" });
    fireEvent.change(input, { target: { value: "more excited, wave hello" } });
    fireEvent.submit(input);
    await waitFor(() => expect(mocks.sendChat).toHaveBeenCalled());
    expect(mocks.sendChat.mock.calls[0].slice(0, 3)).toEqual([
      "shot-demo", "more excited, wave hello\n\n[Story view · Shot view of scene 2, s2 “Gallery map” (animation) · direct only this scene's shot, with set_shot]", false,
    ]);
    expect(screen.getByRole("status", { name: "Direct it" }).textContent).toContain("The Assistant is directing this shot…");
    expect(input.getAttribute("placeholder")).toBe("The Assistant is working…");
    expect((screen.getByRole("textbox", { name: "Direct the shot" }) as HTMLInputElement).disabled).toBe(true);

    act(() => {
      ingestAgentEvent({ ...card("p1", "s2", 1) });
      ingestAgentEvent({ ...card("p0", "s1", 0) });
      ingestAgentEvent({ type: "turn_done" });
    });
    const proposals = screen.getAllByRole("group", { name: /^Proposal:/ });
    expect(proposals.map((p) => p.getAttribute("aria-label"))).toEqual(["Proposal: Animate scene 2"]);

    const accepted = { ...PROJECT, scenes: [PROJECT.scenes![0], { ...PROJECT.scenes![1], shot: { mode: "animation" as const, expression: "excited" } }] };
    mocks.acceptProposal.mockResolvedValue({ ok: true, project: { ...accepted, revision: 4 }, revision: 4, proposal: {} });
    fireEvent.click(within(proposals[0]).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(shotOf("s2")).toEqual({ mode: "animation", expression: "excited" }));
    expect(mocks.acceptProposal).toHaveBeenCalledWith("shot-demo", "p1", undefined);
    expect(screen.queryByRole("group", { name: "Proposal: Animate scene 2" })).toBeNull();
    expect(takes()).toEqual(["1", "2*"]);
  });

  it("says what came back when the turn proposes nothing, and waits for a provider", async () => {
    await openSheet("s2");
    const input = screen.getByRole("textbox", { name: "Direct the shot" }) as HTMLInputElement;
    expect(input.disabled).toBe(true);
    act(() => useChatStore.setState({ chatConfig: READY }));
    fireEvent.change(input, { target: { value: "make it purple" } });
    fireEvent.submit(input);
    await waitFor(() => expect(mocks.sendChat).toHaveBeenCalled());
    act(() => {
      ingestAgentEvent({ type: "chat_text", text: "The default rig has no purple palette, so I left the shot as it is." });
      ingestAgentEvent({ type: "turn_done" });
    });
    expect(screen.getByRole("status", { name: "Direct it" }).textContent).toBe("The default rig has no purple palette, so I left the shot as it is.");
  });

  it.each(["authentication_required", "assistant_not_installed", "provider_unavailable"])("shows the real HTTP refusal detail for %s", async (code) => {
    const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
    useChatStore.setState({ chatConfig: READY });
    await openSheet("s2");
    const detail = "Open Settings, Add-ons to restore the assistant.";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code, error: detail }), {
      status: 409, statusText: "Conflict",
    })));
    mocks.sendChat.mockImplementation(actual.api.sendChat);
    const input = screen.getByRole("textbox", { name: "Direct the shot" });
    fireEvent.change(input, { target: { value: "wave hello" } });
    fireEvent.submit(input);
    await waitFor(() => expect(screen.getByRole("status", { name: "Direct it" }).textContent).toBe(detail));
    expect(screen.queryByText(/409 Conflict/)).toBeNull();
  });
});

describe("stage captions", () => {
  it("draws the player's pill page at the stage's time, words coloured as spoken", () => {
    const calls: string[] = [];
    const ctx = {
      canvas: { width: 960, height: 540 },
      save: () => {}, restore: () => {}, setTransform: (...a: number[]) => calls.push(`transform ${a.join(",")}`),
      beginPath: () => {}, roundRect: (...a: number[]) => calls.push(`box ${a.map((n) => Math.round(n)).join(",")}`), fill: () => calls.push("fill"),
      measureText: (t: string) => ({ width: t.length * 10 }), fillText: (t: string, x: number, y: number) => calls.push(`${ctx.fillStyle} ${t} ${Math.round(x)},${Math.round(y)}`),
      fillStyle: "", font: "", textBaseline: "", textAlign: "", fontKerning: "",
    };
    const pages: PillPage[] = [{ on: 100, off: 400, words: [{ w: "Read", start: 100, end: 200 }, { w: "the", start: 200, end: 300 }] }];
    drawPillCaption(ctx as unknown as CanvasRenderingContext2D, pages, 2.5, 1920, 1080, '"Inter"');
    expect(ctx.font).toMatch(/^700 [\d.]+px "Inter"$/);
    expect(calls[0]).toBe("transform 0.5,0,0,0.5,0,0");
    expect(calls.slice(-2).map((c) => c.split(" ").slice(0, 2).join(" "))).toEqual(["#f4f6f7 Read", "#f5c518 the"]);
    const empty: string[] = [];
    drawPillCaption({ ...ctx, setTransform: () => empty.push("x") } as unknown as CanvasRenderingContext2D, pages, 5, 1920, 1080, "x");
    expect(empty).toEqual([]);
  });
});

describe("draft expressions", () => {
  it("stay out of the Shot view's expression picker until approved", async () => {
    const cast = { ...NO_CAST, characters: [{ ...DEFAULT_PROFILE, expressions: [
      { id: "smug", name: "Smug", spec: { eyes: "side", mouth: "smile" }, createdAt: "t" },
      { id: "custom-proud", name: "Custom proud", spec: { eyes: "arc", mouth: "smile" }, createdAt: "t", draft: true as const },
    ] }] };
    useCastStore.setState({ scopes: { [scopeKey({ workspace: null })]: { cast, status: "ready", error: null } } });
    await openSheet("s2");
    const names = within(group("Expression")).getAllByRole("button").map((b) => b.getAttribute("title"));
    expect(names).toContain("Smug");
    expect(names).not.toContain("Custom proud");
    expect(useStory.getState().shotSceneId).toBe("s2");
  });
});
