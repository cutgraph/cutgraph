import { describe, it, expect } from "vitest";
import {
  applyAgentEvent,
  armsAutoApprove,
  hydrateFromLog,
  initialChatState,
  isTerminalStatus,
  mergeJobs,
  mergeProposals,
  reconcileFromLog,
} from "../src/chat/agentEvents";

describe("applyAgentEvent — pure reducer", () => {
  it("chat_text appends an assistant message and opens the turn", () => {
    const s = applyAgentEvent(initialChatState(), { type: "chat_text", text: "on it" });
    expect(s.messages).toEqual([{ role: "assistant", text: "on it" }]);
    expect(s.turnActive).toBe(true);
  });

  it("tool_use pushes a chip", () => {
    const s = applyAgentEvent(initialChatState(), {
      type: "tool_use",
      name: "mcp__editor__get_project",
      input: {},
    });
    expect(s.toolChips).toEqual([{ name: "mcp__editor__get_project", input: {} }]);
  });

  it("proposal registers a card keyed by id, later status update greys it", () => {
    let s = applyAgentEvent(initialChatState(), {
      type: "proposal",
      id: "abc",
      title: "Tighten intro",
      rationale: "3 filler restarts",
      savesSec: 6.2,
      status: "pending",
      baseRevision: 7,
    });
    expect(s.proposals.abc.title).toBe("Tighten intro");
    expect(s.proposals.abc.status).toBe("pending");
    expect(s.proposals.abc.baseRevision).toBe(7);

    s = applyAgentEvent(s, { type: "proposal", id: "abc", status: "accepted" });
    expect(s.proposals.abc.status).toBe("accepted");
    expect(s.proposals.abc.title).toBe("Tighten intro"); // preserved across the status-only update
  });

  it("agent_error sets the banner and closes the turn", () => {
    const s = applyAgentEvent(initialChatState(), {
      type: "agent_error",
      kind: "auth",
      message: "Key not verified. Open Settings to verify your Anthropic API key.",
    });
    expect(s.authError).toContain("Settings");
    expect(s.turnActive).toBe(false);
  });

  it("turn_done clears turnActive", () => {
    const opened = applyAgentEvent(initialChatState(), { type: "chat_text", text: "hi" });
    expect(opened.turnActive).toBe(true);
    const done = applyAgentEvent(opened, { type: "turn_done" });
    expect(done.turnActive).toBe(false);
  });

  it("ignores unknown event types", () => {
    const s0 = initialChatState();
    expect(applyAgentEvent(s0, { type: "totally_unknown" })).toBe(s0);
  });
});

describe("job — render chip lifecycle", () => {
  it("registers a render_final chip needing approval, then advances its status", () => {
    let s = applyAgentEvent(initialChatState(), {
      type: "job",
      id: "j1",
      kind: "render_final",
      status: "needs-approval",
    });
    expect(s.jobs.j1).toMatchObject({
      id: "j1", kind: "render_final", status: "needs-approval",
    });

    s = applyAgentEvent(s, { type: "job", id: "j1", kind: "render_final", status: "queued" });
    expect(s.jobs.j1.status).toBe("queued");
    s = applyAgentEvent(s, { type: "job", id: "j1", kind: "render_final", status: "running", progress: 0.4 });
    expect(s.jobs.j1.status).toBe("running");
    expect(s.jobs.j1.progress).toBe(0.4);
    s = applyAgentEvent(s, { type: "job", id: "j1", kind: "render_final", status: "done" });
    expect(s.jobs.j1.status).toBe("done");
  });

  it("registers a render_preview_segment chip keyed by its output path (no jobId)", () => {
    const s = applyAgentEvent(initialChatState(), {
      type: "job",
      id: "/media/preview/abc.mp4",
      kind: "render_preview_segment",
      status: "done",
      output: "/media/preview/abc.mp4",
    });
    expect(s.jobs["/media/preview/abc.mp4"].kind).toBe("render_preview_segment");
    expect(s.jobs["/media/preview/abc.mp4"].status).toBe("done");
  });

  it("ignores a job event with no id (nothing to key the chip on)", () => {
    const s0 = initialChatState();
    expect(applyAgentEvent(s0, { type: "job", kind: "render_final", status: "queued" })).toBe(s0);
  });

  it("deduplicates stale SSE events", () => {
    let s = applyAgentEvent(initialChatState(), {
      type: "job", id: "j2", kind: "transcribe", status: "running",
      progress: 0.4, progressStage: "Computing", sequence: 3,
    });
    s = applyAgentEvent(s, {
      type: "job", id: "j2", kind: "transcribe", status: "queued",
      sequence: 2,
    });
    s = applyAgentEvent(s, {
      type: "job", id: "j2", status: "queued",
    });
    expect(s.jobs.j2.status).toBe("running");

    s = applyAgentEvent(s, {
      type: "job", id: "j2", status: "done", progress: 1,
      progressStage: "Complete", sequence: 4,
    });
    expect(s.jobs.j2.status).toBe("done");
  });

  it("merges equal-sequence authoritative metadata after SSE and clears retry state on snapshot", () => {
    let s = applyAgentEvent(initialChatState(), {
      type: "job", id: "live-1", kind: "render_final", status: "retrying",
      attemptState: "retrying", sequence: 4,
    });
    s = applyAgentEvent(s, {
      type: "job", id: "live-1", kind: "render_final", status: "retrying",
      createdAt: "2026-08-25T12:00:00.123456Z", createdOrdinal: 99, sequence: 4,
    });
    expect(s.jobs["live-1"].createdAt).toBe("2026-08-25T12:00:00.123456Z");
    expect(s.jobs["live-1"].createdOrdinal).toBe(99);

    s = mergeJobs(s, [{
      id: "live-1", kind: "render_final", status: "running", sequence: 4,
      progress: 0.35, progressStage: "Computing",
    }]);
    expect(s.jobs["live-1"].status).toBe("running");
    expect(s.jobs["live-1"].attemptState).toBeUndefined();
  });
});

describe("options — judge/choose gallery", () => {
  it("registers a gallery, then a resolved event greys it while keeping its cards", () => {
    let s = applyAgentEvent(initialChatState(), {
      type: "options",
      kind: "hook",
      options: [
        { label: "Ask a question", description: "open with the objection" },
        { label: "Cold cut", previewRef: "/media/preview/x.mp4" },
      ],
    });
    expect(s.options?.kind).toBe("hook");
    expect(s.options?.options.length).toBe(2);
    expect(s.options?.resolved).toBe(null);

    s = applyAgentEvent(s, { type: "options", kind: "hook", resolved: "Cold cut" });
    expect(s.options?.resolved).toBe("Cold cut");
    expect(s.options?.options.length).toBe(2); // cards preserved so the picked one can highlight
  });

  it("a resolved event with no live gallery is a no-op", () => {
    const s0 = initialChatState();
    expect(applyAgentEvent(s0, { type: "options", resolved: "A" })).toBe(s0);
  });

  it("a fresh gallery event replaces a prior resolved one", () => {
    let s = applyAgentEvent(initialChatState(), {
      type: "options",
      kind: "hook",
      options: [{ label: "A" }],
    });
    s = applyAgentEvent(s, { type: "options", kind: "hook", resolved: "A" });
    s = applyAgentEvent(s, { type: "options", kind: "caption", options: [{ label: "B" }] });
    expect(s.options?.kind).toBe("caption");
    expect(s.options?.resolved).toBe(null);
  });
});

describe("armsAutoApprove — exact-match heuristic", () => {
  it("arms only on the exact phrases", () => {
    for (const t of ["just do it", "Just do it.", "GO AHEAD", "apply it!"]) {
      expect(armsAutoApprove(t)).toBe(true);
    }
  });
  it("does not arm on anything else", () => {
    for (const t of ["just do it now", "please go ahead and cut", "apply it to clip 3", "do it"]) {
      expect(armsAutoApprove(t)).toBe(false);
    }
  });
});

describe("mergeProposals — durable store overlays the log-rebuilt cards (reconnect)", () => {
  it("the proposal store's authoritative status wins over the log's creation status", () => {
    // Log replayed the card at creation (pending); the durable store says it was accepted since.
    const s0 = applyAgentEvent(initialChatState(), {
      type: "proposal",
      id: "p1",
      title: "Cut fillers",
      status: "pending",
    });
    const s = mergeProposals(s0, [{ id: "p1", title: "Cut fillers", status: "accepted", savesSec: 6 }]);
    expect(s.proposals.p1.status).toBe("accepted");
    expect(s.proposals.p1.savesSec).toBe(6);
  });

  it("registers cards the log never saw and ignores malformed rows", () => {
    const s = mergeProposals(initialChatState(), [
      { id: "p2", title: "Trim", status: "stale" },
      { nope: true },
    ]);
    expect(s.proposals.p2.status).toBe("stale");
    expect(Object.keys(s.proposals)).toEqual(["p2"]);
  });
});

describe("hydrateFromLog — reconnect reconcile through the same reducer", () => {
  it("rebuilds user + agent turns and proposals, turn settled", () => {
    const s = hydrateFromLog([
      { role: "user", text: "tighten this" },
      { role: "agent", type: "chat_text", text: "blindspot: 3 filler runs" },
      { role: "agent", type: "proposal", id: "p1", title: "Cut fillers", status: "pending" },
    ]);
    expect(s.messages).toEqual([
      { role: "user", text: "tighten this" },
      { role: "assistant", text: "blindspot: 3 filler runs" },
    ]);
    expect(s.proposals.p1.title).toBe("Cut fillers");
    expect(s.turnActive).toBe(false);
  });
});

describe("agent_error — classified kind + turn disarm", () => {
  it("kind:'auth' is recorded as errorKind so the panel can show the persistent connection banner", () => {
    const s = applyAgentEvent(initialChatState(), {
      type: "agent_error",
      kind: "auth",
      message: "authentication_failed",
    });
    expect(s.errorKind).toBe("auth");
    expect(s.authError).toBe("authentication_failed");
    expect(s.turnActive).toBe(false);
  });

  it("an unknown/absent kind falls back to 'runtime'", () => {
    expect(applyAgentEvent(initialChatState(), { type: "agent_error", message: "boom" }).errorKind).toBe(
      "runtime",
    );
  });

  it("a turn-ending error disarms an armed auto-accept (the arm must not outlive its turn)", () => {
    const armed = { ...initialChatState(), autoApproveArmed: true, armedTurnId: 3, turnActive: true };
    const s = applyAgentEvent(armed, { type: "agent_error", kind: "runtime", message: "x" });
    expect(s.autoApproveArmed).toBe(false);
    expect(s.armedTurnId).toBe(null);
  });

  it("a following chat_text clears the auth banner (the session proved it is authenticated)", () => {
    let s = applyAgentEvent(initialChatState(), { type: "agent_error", kind: "auth", message: "authentication_failed" });
    s = applyAgentEvent(s, { type: "chat_text", text: "back online" });
    expect(s.errorKind).toBe(null);
    expect(s.authError).toBe(null);
  });
});

describe("reconcile terminal-wins — a reconnect never reverts an applied card", () => {
  it("reconcileFromLog preserves a locally-accepted card over the log's creation-pending", () => {
    let live = applyAgentEvent(initialChatState(), {
      type: "proposal",
      id: "p1",
      title: "Cut",
      status: "pending",
      ops: [],
    });
    live = applyAgentEvent(live, { type: "proposal", id: "p1", status: "accepted" }); // just applied
    expect(live.proposals.p1.status).toBe("accepted");

    const rebuilt = reconcileFromLog(live, [
      { role: "agent", type: "proposal", id: "p1", title: "Cut", status: "pending" },
    ]);
    expect(rebuilt.proposals.p1.status).toBe("accepted"); // local terminal preserved
  });

  it("mergeProposals: a durable pending never reverts a local terminal, but a durable terminal wins", () => {
    const accepted = mergeProposals(
      { ...initialChatState(), proposals: { p1: { id: "p1", title: "Cut", status: "accepted" } } },
      [{ id: "p1", status: "pending" }],
    );
    expect(accepted.proposals.p1.status).toBe("accepted"); // durable pending does NOT revert

    const rejected = mergeProposals(
      { ...initialChatState(), proposals: { p1: { id: "p1", title: "Cut", status: "pending" } } },
      [{ id: "p1", status: "rejected" }],
    );
    expect(rejected.proposals.p1.status).toBe("rejected"); // durable terminal wins over local pending
  });

  it("reconcileFromLog replaces the transcript from the log tail (dedup: no duplicated messages)", () => {
    const live = applyAgentEvent(initialChatState(), { type: "chat_text", text: "streamed once" });
    const rebuilt = reconcileFromLog(live, [{ role: "agent", type: "chat_text", text: "streamed once" }]);
    expect(rebuilt.messages).toEqual([{ role: "assistant", text: "streamed once" }]);
  });

  it("isTerminalStatus flags only accepted/rejected/stale", () => {
    expect(["accepted", "rejected", "stale"].every(isTerminalStatus)).toBe(true);
    expect(isTerminalStatus("pending")).toBe(false);
  });
});
