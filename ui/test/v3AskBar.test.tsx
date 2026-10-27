// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatConfig, TranscriptDoc, TranscriptWord } from "../src/api";
import type { Project, Scene } from "../src/types/project";

const mocks = vi.hoisted(() => ({
  getChatConfig: vi.fn(),
  sendChat: vi.fn(),
  acceptProposal: vi.fn(),
  setProposalStatus: vi.fn(),
  saveProject: vi.fn(),
  transcript: { doc: null as TranscriptDoc | null },
}));
vi.mock("../src/api", () => ({
  api: {
    getChatConfig: mocks.getChatConfig,
    sendChat: mocks.sendChat,
    acceptProposal: mocks.acceptProposal,
    setProposalStatus: mocks.setProposalStatus,
    saveProject: mocks.saveProject,
    getChatLog: async () => ({ entries: [] }),
    getProposals: async () => ({ proposals: [] }),
    getComputeJobs: async () => ({ jobs: [] }),
  },
}));
vi.mock("../src/transcript/useTranscript", () => ({
  useTranscript: () => ({ doc: mocks.transcript.doc, lifecycle: { state: "ready", job: null }, loading: false, error: null, refresh: () => {} }),
}));

import AskBar from "../src/v3/story/AskBar";
import { turnContext } from "../src/v3/story/ask/ask";
import type { SceneSpan } from "../src/v3/story/model";
import ScriptLens from "../src/v3/story/lenses/ScriptLens";
import { resetStory, setRange, useStory } from "../src/v3/story/storyStore";
import { adoptSaved, undo, useProjectStore } from "../src/store/useProjectStore";
import { ingestAgentEvent, useChatStore } from "../src/store/useChatStore";
import { initialChatState } from "../src/chat/agentEvents";

const MEDIA = "talk.mp4";
const WORDS: TranscriptWord[] = ["So", "um", "this", "is", "the", "plan."].map((w, i) => ({ w, start: i + 0.1, end: i + 0.6, segIdx: 0 }));

function project(clips: Project["tracks"][number]["clips"] = [{ id: "a", media: { source: MEDIA }, srcInF: 0, srcOutF: 210, tlStartF: 0 }]): Project {
  return {
    fps: 30, width: 1920, height: 1080, name: "talk", revision: 5, schemaVersion: 3,
    tracks: [{ id: "speech", kind: "speech", clips }],
    scenes: [
      { id: "s1", title: "Open", role: "Hook", startWord: 0 },
      { id: "s2", title: "Close", role: "Wrap", startWord: 3 },
    ],
  };
}

function config(available: boolean): ChatConfig {
  const availability = available
    ? { available: true, error: null }
    : { available: false, error: { code: "authentication_required", message: "Connect your key in Settings to ask for edits." } };
  return {
    schemaVersion: 1,
    providers: [{
      id: "claude", label: "Claude", availability,
      profiles: [{ id: "default", label: "Default", model: null, efforts: ["low"], modes: ["default", "plan"],
        capabilities: { proposalOnly: true, planReadOnly: true, autoFullAccess: false }, availability }],
    }],
    selectionDefaults: { providerId: "claude", profileId: "default", model: null, effort: null, mode: "default" },
    models: [], efforts: [], modes: [], defaults: { model: null, effort: null, mode: "default" },
  } as unknown as ChatConfig;
}

const PROPOSAL = {
  type: "proposal", id: "p1", title: "Cut 1 filler word", rationale: "“um” · saves 0.5s", savesSec: 0.5, status: "pending",
  baseRevision: 5, ops: [],
  marks: [{ a: 1, b: 2, kind: "cut", label: "“um”" }, { a: 3, b: 5, kind: "mark", label: "B-roll: plate" }],
};

function renderStory(available = true, narration?: Project["narration"]) {
  mocks.getChatConfig.mockResolvedValue(config(available));
  mocks.transcript.doc = { language: "en", duration: 6, words: WORDS, segments: [], fillers: [] };
  adoptSaved(narration ? { ...project(), narration } : project(), 5);
  return render(<><ScriptLens /><AskBar /></>);
}

beforeEach(() => {
  for (const m of [mocks.getChatConfig, mocks.sendChat, mocks.acceptProposal, mocks.setProposalStatus, mocks.saveProject]) m.mockReset();
  mocks.sendChat.mockResolvedValue({ ok: true });
  resetStory();
  useChatStore.setState({ ...initialChatState(), chatConfig: null });
  useProjectStore.setState({ project: null, revision: 0, playheadF: 0 });
  useProjectStore.temporal.getState().clear();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const askInput = () => screen.getByRole("textbox", { name: "Ask the Assistant" });
const sug = (c: HTMLElement) => [...c.querySelectorAll(".sugs button")].map((b) => b.textContent);

describe("Ask bar states", () => {
  it.each([
    ["assistant_not_installed", "Assistant isn't installed. Open Settings, Add-ons.", "Open Settings, Add-ons"],
    ["authentication_required", "Connect your key in Settings to ask for edits.", "Open Settings, Add-ons"],
    ["authentication_required", "Key not verified. Open Settings to verify your Anthropic API key.", "Open Settings, Add-ons"],
  ])("shows the actionable readiness state: %s %s", async (code, message, linkLabel) => {
    const unavailable = config(false);
    const availability = { available: false, error: { code: String(code), message: String(message) } };
    unavailable.providers[0].availability = availability;
    unavailable.providers[0].profiles[0].availability = availability;
    mocks.getChatConfig.mockResolvedValue(unavailable);
    adoptSaved(project(), 5);
    render(<AskBar />);
    expect(await screen.findByText(String(message))).toBeTruthy();
    expect(screen.getByRole("link", { name: String(linkLabel) }).getAttribute("href"))
      .toContain(`?view=settings#addons`);
    expect(askInput()).toHaveProperty("disabled", true);
    expect(mocks.sendChat).not.toHaveBeenCalled();
  });

  it("an authentication refusal while sending disables Ask and links to Settings", async () => {
    renderStory();
    await waitFor(() => expect((askInput() as HTMLInputElement).disabled).toBe(false));
    mocks.sendChat.mockRejectedValue(Object.assign(new Error("Connect your key in Settings to ask for edits."), {
      code: "authentication_required",
      detail: "Connect your key in Settings to ask for edits.",
    }));
    fireEvent.change(askInput(), { target: { value: "hello" } });
    fireEvent.submit(askInput().closest("form")!);
    const link = await screen.findByRole("link", { name: "Open Settings, Add-ons" });
    expect(link.getAttribute("href")).toContain("view=settings");
    expect((askInput() as HTMLInputElement).disabled).toBe(true);
    expect(useChatStore.getState().errorKind).toBe("auth");
    expect(useChatStore.getState().turnActive).toBe(false);
  });

  it("without a ready provider it says so, links to Settings and offers no dead controls", async () => {
    const { container } = renderStory(false);
    expect(await screen.findByText("Connect your key in Settings to ask for edits.")).toBeTruthy();
    const link = screen.getByRole("link", { name: "Open Settings, Add-ons" });
    expect(link.getAttribute("href")).toContain("view=settings");
    expect((askInput() as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("Cutgraph Assistant · Powered by Claude")).toBeTruthy();
    expect(sug(container)).toEqual([]);
    expect(container.querySelector(".ask-offline")?.textContent).not.toContain("Connect your key to ask for edits.");
  });

  it("shows the server refusal after removal and recovers after the config refresh", async () => {
    renderStory();
    await waitFor(() => expect(askInput()).toHaveProperty("disabled", false));
    const message = "Assistant isn't installed. Open Settings, Add-ons.";
    mocks.sendChat.mockRejectedValue(Object.assign(new Error(`409 Conflict: ${message}`), { code: "assistant_not_installed", detail: message }));
    const removed = config(false);
    const availability = { available: false, error: { code: "assistant_not_installed", message } };
    removed.providers[0].availability = availability;
    removed.providers[0].profiles[0].availability = availability;
    mocks.getChatConfig.mockResolvedValue(removed);
    fireEvent.change(askInput(), { target: { value: "hello" } });
    fireEvent.submit(askInput().closest("form")!);
    expect(await screen.findByText(message, { selector: ".ask-offline b" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Settings, Add-ons" }).getAttribute("href")).toContain("#addons");
    expect(askInput()).toHaveProperty("disabled", true);
    expect(useChatStore.getState().errorKind).toBe("assistant_not_installed");
    await waitFor(() => expect(mocks.getChatConfig).toHaveBeenCalledTimes(2), { timeout: 3000 });
    expect(screen.getByText(message, { selector: ".ask-offline b" })).toBeTruthy();
    expect(useChatStore.getState().errorKind).toBeNull();
    mocks.getChatConfig.mockResolvedValue(config(true));
    await waitFor(() => expect(askInput()).toHaveProperty("disabled", false), { timeout: 3000 });
    expect(screen.queryByText(message)).toBeNull();
  });

  it.each(["auth", "assistant_not_installed"])("shows the server agent_error detail for %s", async (kind) => {
    renderStory();
    await waitFor(() => expect(askInput()).toHaveProperty("disabled", false));
    const message = "Restart Studio to enable the assistant. Open Settings, Add-ons.";
    act(() => ingestAgentEvent({ type: "agent_error", kind, message }));
    expect(screen.getByText(message, { selector: ".ask-offline b" })).toBeTruthy();
  });

  it.each(["authentication_required", "assistant_not_installed", "provider_unavailable"])("shows the real HTTP refusal detail for %s", async (code) => {
    const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
    renderStory();
    await waitFor(() => expect(askInput()).toHaveProperty("disabled", false));
    const detail = "Open Settings, Add-ons to restore the assistant.";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code, error: detail }), {
      status: 409, statusText: "Conflict",
    })));
    mocks.sendChat.mockImplementation(actual.api.sendChat);
    fireEvent.change(askInput(), { target: { value: "hello" } });
    fireEvent.submit(askInput().closest("form")!);
    await waitFor(() => expect(useChatStore.getState().authError).toBe(detail));
    expect(screen.getAllByText(detail).length).toBeGreaterThan(0);
    expect(screen.queryByText(/409 Conflict/)).toBeNull();
  });

  it("keeps a streamed auth failure offline through unchanged ready polls until a key action", async () => {
    renderStory();
    await waitFor(() => expect(askInput()).toHaveProperty("disabled", false));
    const message = "Key not verified. Open Settings to verify your Anthropic API key.";
    act(() => ingestAgentEvent({ type: "agent_error", kind: "auth", message }));
    await waitFor(() => expect(mocks.getChatConfig.mock.calls.length).toBeGreaterThanOrEqual(3), { timeout: 4000 });
    expect(askInput()).toHaveProperty("disabled", true);
    expect(screen.getByText(message, { selector: ".ask-offline b" })).toBeTruthy();
    const changedModel = config(true);
    changedModel.providers[0].profiles[0].model = "another-model";
    act(() => useChatStore.getState().setChatConfig(changedModel));
    expect(askInput()).toHaveProperty("disabled", true);
    act(() => useChatStore.setState({ chatConfig: null, errorKind: null, authError: null }));
    await waitFor(() => expect(askInput()).toHaveProperty("disabled", false));
    expect(screen.queryByText(message)).toBeNull();
  });

  it("recovers from a streamed auth failure when verification changes availability", async () => {
    renderStory();
    await waitFor(() => expect(askInput()).toHaveProperty("disabled", false));
    act(() => ingestAgentEvent({ type: "agent_error", kind: "auth", message: "Key not verified." }));
    mocks.getChatConfig.mockResolvedValue(config(false));
    await waitFor(() => expect(useChatStore.getState().chatConfig?.providers[0].availability.available).toBe(false), { timeout: 3000 });
    expect(askInput()).toHaveProperty("disabled", true);
    mocks.getChatConfig.mockResolvedValue(config(true));
    await waitFor(() => expect(askInput()).toHaveProperty("disabled", false), { timeout: 3000 });
  });

  it("stays disabled while credentials are checking, then enables after readiness refresh", async () => {
    const checking = config(false);
    const availability = { available: false, error: { code: "authentication_checking", message: "Verifying your key…" } };
    checking.providers[0].availability = availability;
    checking.providers[0].profiles[0].availability = availability;
    mocks.getChatConfig.mockResolvedValue(checking);
    adoptSaved(project(), 5);
    render(<AskBar />);
    await waitFor(() => expect(mocks.getChatConfig).toHaveBeenCalled());
    expect(screen.queryByText(/Key not verified|Connect your key/)).toBeNull();
    expect((askInput() as HTMLInputElement).disabled).toBe(true);
    mocks.getChatConfig.mockResolvedValue(config(true));
    await waitFor(() => expect((askInput() as HTMLInputElement).disabled).toBe(false), { timeout: 3000 });
  });

  it("idle: one line input and three chips, grounded once words are selected", async () => {
    const { container } = renderStory();
    await waitFor(() => expect((askInput() as HTMLInputElement).disabled).toBe(false));
    expect(askInput().getAttribute("placeholder")).toBe("Ask the Assistant…");
    expect(sug(container)).toEqual(["Cut filler words", "Add B-roll here", "Add music"]);
    act(() => setRange({ a: 2, b: 4 }));
    expect(sug(container)).toEqual(["Tighten this", "Add B-roll here", "Make this an animation"]);
    expect(container.querySelector(".ask-sheet")).toBeNull();
  });

  it("an animation video gets its own fixed chips regardless of the selection", async () => {
    const { container } = renderStory(true, "estimated");
    await waitFor(() => expect((askInput() as HTMLInputElement).disabled).toBe(false));
    expect(sug(container)).toEqual(["Make your character excited", "Tighten this scene", "Make it vertical"]);
    act(() => setRange({ a: 2, b: 4 }));
    expect(sug(container)).toEqual(["Make your character excited", "Tighten this scene", "Make it vertical"]);
  });

  it("Enter sends the turn with the selection as context and streams the reply into the sheet", async () => {
    const { container } = renderStory();
    await waitFor(() => expect((askInput() as HTMLInputElement).disabled).toBe(false));
    act(() => setRange({ a: 3, b: 5 }));
    await userEvent.type(askInput(), "tighten this{Enter}");
    expect(mocks.sendChat).toHaveBeenCalledWith(
      "talk",
      "tighten this\n\n[Story view · selected words 3-5 “is the plan.” in s2 “Close” (footage)]",
      false,
      { providerId: "claude", profileId: "default", model: null, effort: null, mode: "default" },
    );
    const sheet = container.querySelector(".ask-sheet") as HTMLElement;
    expect(within(sheet).getByRole("status").textContent).toContain("Working…");
    expect((askInput() as HTMLInputElement).disabled).toBe(true);
    act(() => ingestAgentEvent({ type: "tool_use", name: "mcp__editor__cut_fillers", input: {} }));
    expect(askInput().getAttribute("placeholder")).toBe("The Assistant is working…");
    act(() => ingestAgentEvent({ type: "chat_text", text: "That aside can go." }));
    expect(within(sheet).getByText("tighten this")).toBeTruthy();
    expect(within(sheet).getByText("Finding cuts")).toBeTruthy();
    expect(within(sheet).getByText("That aside can go.")).toBeTruthy();
    act(() => ingestAgentEvent({ type: "turn_done" }));
    expect(within(sheet).getByRole("status").textContent).toBe("Replied");
    fireEvent.click(within(sheet).getByRole("button", { expanded: true }));
    expect(within(sheet).queryByText("That aside can go.")).toBeNull();
  });

  it("shows a friendly tool name instead of the raw id, and renders the reply's markdown", async () => {
    const { container } = renderStory();
    await waitFor(() => expect((askInput() as HTMLInputElement).disabled).toBe(false));
    await userEvent.type(askInput(), "cut the filler words{Enter}");
    const sheet = container.querySelector(".ask-sheet") as HTMLElement;
    act(() => ingestAgentEvent({ type: "tool_use", name: "mcp__editor__get_channel", input: {} }));
    act(() => ingestAgentEvent({ type: "chat_text", text: "I found **2 filler words**: `um`, `uh`." }));
    expect(within(sheet).queryByText("get channel")).toBeNull();
    expect(within(sheet).getByText("Reading the channel profile")).toBeTruthy();
    expect(within(sheet).queryByText(/\*\*2 filler words\*\*/)).toBeNull();
    expect(within(sheet).getByText("2 filler words").tagName).toBe("STRONG");
    expect(within(sheet).getByText("um").tagName).toBe("CODE");
  });

  it("a proposal is a card whose words light up in the script until it is settled", async () => {
    const { container } = renderStory();
    await waitFor(() => expect((askInput() as HTMLInputElement).disabled).toBe(false));
    act(() => ingestAgentEvent(PROPOSAL));
    const card = screen.getByRole("group", { name: "Proposal: Cut 1 filler word" });
    expect(within(card).getByText("saves 0.5s")).toBeTruthy();
    expect(within(card).getByText("“um” · B-roll: plate")).toBeTruthy();
    expect(container.querySelector('[data-word-idx="1"]')?.className).toContain("sug");
    expect(container.querySelector('[data-word-idx="3"]')?.className).toContain("prop");
    expect(container.querySelector('[data-word-idx="0"]')?.className).toBe("w");
    mocks.setProposalStatus.mockResolvedValue({ id: "p1", status: "rejected" });
    fireEvent.click(within(card).getByRole("button", { name: "Reject" }));
    expect(mocks.setProposalStatus).toHaveBeenCalledWith("talk", "p1", "rejected", "Rejected in the Story view");
    expect(screen.queryByRole("group", { name: "Proposal: Cut 1 filler word" })).toBeNull();
    expect(useStory.getState().proposalWords).toBeNull();
    expect(container.querySelector('[data-word-idx="1"]')?.className).toBe("w");
  });

  it("Accept applies through the accept path as one undo step that Ctrl+Z-style undo restores", async () => {
    const { container } = renderStory();
    await waitFor(() => expect((askInput() as HTMLInputElement).disabled).toBe(false));
    act(() => ingestAgentEvent(PROPOSAL));
    const cut = project([
      { id: "a", media: { source: MEDIA }, srcInF: 0, srcOutF: 33, tlStartF: 0 },
      { id: "a-r48", media: { source: MEDIA }, srcInF: 48, srcOutF: 210, tlStartF: 33 },
    ]);
    mocks.acceptProposal.mockResolvedValue({ ok: true, project: { ...cut, revision: 6 }, revision: 6, proposal: {} });
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(useChatStore.getState().proposals.p1.status).toBe("accepted"));
    expect(mocks.acceptProposal).toHaveBeenCalledWith("talk", "p1", undefined);
    expect(useProjectStore.getState().revision).toBe(6);
    expect(container.querySelector('[data-word-idx="1"]')?.className).toContain("cut");
    expect(screen.queryByRole("group", { name: "Proposal: Cut 1 filler word" })).toBeNull();
    expect(useProjectStore.temporal.getState().pastStates).toHaveLength(1);
    act(() => undo());
    expect(useProjectStore.getState().project?.tracks[0].clips).toHaveLength(1);
    expect(container.querySelector('[data-word-idx="1"]')?.className).toBe("w");
  });

  it("an out-of-date proposal can be rejected but not accepted", async () => {
    renderStory();
    await waitFor(() => expect((askInput() as HTMLInputElement).disabled).toBe(false));
    act(() => ingestAgentEvent({ ...PROPOSAL, baseRevision: 4 }));
    const card = screen.getByRole("group", { name: "Proposal: Cut 1 filler word" });
    expect(within(card).getByText("Out of date · the video changed")).toBeTruthy();
    expect((within(card).getByRole("button", { name: "Accept" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(card).getByRole("button", { name: "Reject" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("a send that never reaches the server settles the turn with an error, not a stuck spinner", async () => {
    const { container } = renderStory();
    await waitFor(() => expect((askInput() as HTMLInputElement).disabled).toBe(false));
    mocks.sendChat.mockRejectedValue(new Error("offline"));
    await userEvent.click(screen.getByRole("button", { name: "Cut filler words" }));
    await waitFor(() => expect(container.querySelector(".ask-error")?.textContent).toContain("could not be sent"));
    expect((askInput() as HTMLInputElement).disabled).toBe(false);
  });
});

describe("Ask bar context", () => {
  const span = (id: string, startWord: number, endWord: number, shot?: Scene["shot"]): SceneSpan => ({
    scene: { id, title: id.toUpperCase(), role: "Beat", startWord, ...(shot && { shot }) },
    index: 0, startWord, endWord, srcStartF: 0, srcEndF: 0, durF: 0, tlStartF: 0,
  });
  const words: TranscriptWord[] = ["a", "b", "c", "d"].map((w, i) => ({ w, start: i, end: i + 0.5, segIdx: 0 }));

  it("names the scene's picture, so a look asked for on footage comes back as a cameo or animation", () => {
    const spans = [span("s1", 0, 2, { pose: "wave" }), span("s2", 2, 3, { mode: "cameo" }), span("s3", 3, 4, { mode: "animation" })];
    expect(turnContext(words, spans, null, 1)).toBe("\n\n[Story view · cursor at word 1 “b” in s1 “S1” (footage)]");
    expect(turnContext(words, spans, { a: 2, b: 2 }, null)).toBe("\n\n[Story view · selected words 2-2 “c” in s2 “S2” (cameo)]");
    expect(turnContext(words, spans, null, 3)).toBe("\n\n[Story view · cursor at word 3 “d” in s3 “S3” (animation)]");
  });
});
