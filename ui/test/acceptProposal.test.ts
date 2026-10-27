import { describe, it, expect, vi, beforeEach } from "vitest";
import type { Project } from "../src/types/project";

// Mock the api + toast bus (hoisted). The stores are REAL — we exercise the actual applyLocal
// checkpoint + chat-store staleness wiring, only faking the network and the toast sink.
const { acceptMock, setStatusMock, getProjectMock, getProposalsMock } = vi.hoisted(() => ({
  acceptMock: vi.fn(),
  setStatusMock: vi.fn(),
  getProjectMock: vi.fn(),
  getProposalsMock: vi.fn(),
}));
const toastMock = vi.hoisted(() => vi.fn());
vi.mock("../src/api", () => ({
  api: {
    acceptProposal: acceptMock, setProposalStatus: setStatusMock,
    getProject: getProjectMock, getProposals: getProposalsMock,
  },
}));
vi.mock("../src/toast", () => ({ toast: toastMock }));

import { acceptProposal, maybeAutoAccept } from "../src/chat/acceptProposal";
import { useProjectStore, metaState, adoptSaved } from "../src/store/useProjectStore";
import { isSelfPending } from "../src/selfPending";
import { useChatStore } from "../src/store/useChatStore";
import { initialChatState, pickAutoAccept, type ProposalCard } from "../src/chat/agentEvents";

function proj(name: string): Project {
  return {
    fps: 30,
    width: 1920,
    height: 1080,
    name,
    revision: 3,
    schemaVersion: 1,
    tracks: [
      {
        id: "speech",
        kind: "speech",
        clips: [{ id: "c1", media: { source: "s.mp4", proxy: null }, srcInF: 0, srcOutF: 100, tlStartF: 0 }],
      },
    ],
  };
}

function card(turnId?: number): ProposalCard {
  return {
    id: "p1",
    title: "Tighten intro",
    rationale: "3 filler restarts",
    savesSec: 6.2,
    status: "pending",
    ops: [
      { op: "test", path: "/tracks/0/clips/0/id", value: "c1" },
      { op: "replace", path: "/tracks/0/clips/0/srcOutF", value: 60 },
    ],
    turnId,
  };
}

// A deferred whose resolve is captured — lets a test hold a PATCH pending across a double-click.
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const past = () => useProjectStore.temporal.getState().pastStates.length;

beforeEach(() => {
  acceptMock.mockReset();
  setStatusMock.mockReset();
  getProjectMock.mockReset();
  getProposalsMock.mockReset();
  toastMock.mockReset();
  useProjectStore.setState({ project: null, revision: 0 });
  useProjectStore.temporal.getState().clear();
  useChatStore.setState(initialChatState());
});

describe("acceptProposal — the ONE labeled checkpoint", () => {
  it("a successful PATCH records EXACTLY one origin:agent checkpoint and adopts the revision", async () => {
    adoptSaved(proj("local"), 3); // load without recording history
    acceptMock.mockResolvedValueOnce({ ok: true, project: proj("server"), revision: 4, proposal: { id: "p1", status: "accepted" } });

    const past0 = past();
    const meta0 = metaState().past.length;
    await acceptProposal("proj", card());

    expect(past()).toBe(past0 + 1); // exactly one undo delta
    expect(metaState().past.length).toBe(meta0 + 1);
    expect(metaState().past[metaState().past.length - 1]).toEqual({
      origin: "agent",
      label: "Claude: Tighten intro",
    });
    expect(useProjectStore.getState().revision).toBe(4);
    expect(acceptMock).toHaveBeenCalledWith("proj", "p1", undefined);
    expect(setStatusMock).not.toHaveBeenCalled();
  });

  it("a thrown 400 (failed guard) records NO checkpoint and marks the card stale", async () => {
    adoptSaved(proj("local"), 3);
    acceptMock.mockRejectedValue(new Error("409 Conflict: stale base revision"));
    getProjectMock.mockResolvedValue({ project: proj("local"), revision: 3 });
    getProposalsMock.mockResolvedValue({ proposals: [{ id: "p1", status: "stale" }] });
    setStatusMock.mockResolvedValue({ id: "p1", status: "stale" });

    const past0 = past();
    await acceptProposal("proj", card());

    expect(past()).toBe(past0); // no checkpoint — the edit never applied
    expect(useChatStore.getState().proposals.p1.status).toBe("stale");
    expect(setStatusMock).toHaveBeenCalledWith("proj", "p1", "stale");
  });

  it("a 409 revision race also marks the card stale (no checkpoint)", async () => {
    adoptSaved(proj("local"), 3);
    acceptMock.mockRejectedValue(new Error("409 Conflict: revision race"));
    getProjectMock.mockResolvedValue({ project: proj("local"), revision: 3 });
    getProposalsMock.mockResolvedValue({ proposals: [{ id: "p1", status: "stale" }] });
    setStatusMock.mockResolvedValue({ id: "p1", status: "stale" });

    const past0 = past();
    await acceptProposal("proj", card());

    expect(past()).toBe(past0);
    expect(useChatStore.getState().proposals.p1.status).toBe("stale");
  });
});

describe("maybeAutoAccept — arm binds to ONE turn", () => {
  it("auto-accepts the same-turn pending proposal exactly once, logging it as auto-applied", async () => {
    adoptSaved(proj("local"), 3);
    acceptMock.mockResolvedValue({ ok: true, project: proj("server"), revision: 4, proposal: { id: "p1", status: "accepted" } });
    useChatStore.setState({ autoApproveArmed: true, armedTurnId: 7 });

    const fired = await maybeAutoAccept("proj", card(7)); // card created in the armed turn
    expect(fired).toBe(true);
    expect(acceptMock).toHaveBeenCalledTimes(1);
    expect(useChatStore.getState().autoApproveArmed).toBe(false); // disarmed after one use
    expect(useChatStore.getState().armedTurnId).toBe(null);
    expect(acceptMock).toHaveBeenCalledWith("proj", "p1", "auto-applied on user command");

    // A second call is a no-op — arming is spent.
    const again = await maybeAutoAccept("proj", card(7));
    expect(again).toBe(false);
    expect(acceptMock).toHaveBeenCalledTimes(1);
  });

  it("does NOT auto-accept a proposal from a DIFFERENT turn than the arm (old lingering card)", async () => {
    adoptSaved(proj("local"), 3);
    useChatStore.setState({ autoApproveArmed: true, armedTurnId: 5 });
    // A card left pending from an earlier turn (turnId 2) must never be swept up by a turn-5 arm.
    const fired = await maybeAutoAccept("proj", card(2));
    expect(fired).toBe(false);
    expect(acceptMock).not.toHaveBeenCalled();
    expect(useChatStore.getState().autoApproveArmed).toBe(true); // arm NOT spent on the wrong card
  });

  it("does nothing when not armed", async () => {
    adoptSaved(proj("local"), 3);
    const fired = await maybeAutoAccept("proj", card(1));
    expect(fired).toBe(false);
    expect(acceptMock).not.toHaveBeenCalled();
  });
});

describe("arm-to-turn binding through the reducer (the proposal selection is pickAutoAccept)", () => {
  it("a proposal-less armed turn then a Pick never auto-accepts the Pick's proposal", async () => {
    adoptSaved(proj("local"), 3);
    acceptMock.mockResolvedValue({ ok: true, project: proj("server"), revision: 4, proposal: { id: "p1", status: "accepted" } });
    const cs = useChatStore.getState();

    // Turn 1: user arms "just do it"; the agent asks a clarifying question and emits NO proposal.
    cs.appendUserMessage("just do it");
    cs.ingestAgentEvent({ type: "chat_text", text: "which intro — the cold open or the hook?" });
    expect(useChatStore.getState().autoApproveArmed).toBe(true);
    cs.ingestAgentEvent({ type: "turn_done" }); // disarm at turn end
    expect(useChatStore.getState().autoApproveArmed).toBe(false);
    expect(useChatStore.getState().armedTurnId).toBe(null);

    // A later Pick synthesizes turn 2: chat_text then a proposal. Nothing should auto-fire.
    cs.ingestAgentEvent({ type: "chat_text", text: "pinning the hook" });
    cs.ingestAgentEvent({ type: "proposal", id: "pNew", title: "Pin hook", status: "pending", ops: [] });
    expect(pickAutoAccept(useChatStore.getState())).toBe(null);
    const pNew = useChatStore.getState().proposals.pNew;
    expect(await maybeAutoAccept("proj", pNew)).toBe(false);
    expect(acceptMock).not.toHaveBeenCalled();
  });

  it("pickAutoAccept ignores an older pending card and returns the SAME-turn one once armed", () => {
    const cs = useChatStore.getState();
    // Turn 1 (unarmed): an ignored pending proposal lingers.
    cs.appendUserMessage("tighten this");
    cs.ingestAgentEvent({ type: "proposal", id: "pOld", title: "Old", status: "pending", ops: [] });
    cs.ingestAgentEvent({ type: "turn_done" });
    // Turn 2: user arms; the effect must NOT grab pOld before the new proposal arrives.
    cs.appendUserMessage("just do it");
    expect(pickAutoAccept(useChatStore.getState())).toBe(null); // pOld is from turn 1, not the armed turn
    // The new same-turn proposal is the one selected.
    cs.ingestAgentEvent({ type: "proposal", id: "pNew", title: "New", status: "pending", ops: [] });
    const picked = pickAutoAccept(useChatStore.getState());
    expect(picked?.id).toBe("pNew");
  });
});

describe("acceptProposal — accept-truth flow + in-flight guard", () => {
  it("the atomic accept response settles the card ACCEPTED with one checkpoint", async () => {
    adoptSaved(proj("local"), 3);
    acceptMock.mockResolvedValueOnce({ ok: true, project: proj("server"), revision: 4, proposal: { id: "p1", status: "accepted" } });

    const past0 = past();
    await acceptProposal("proj", card());

    // The edit applied: one checkpoint, revision adopted, card accepted — NOT stale.
    expect(past()).toBe(past0 + 1);
    expect(useProjectStore.getState().revision).toBe(4);
    expect(useChatStore.getState().proposals.p1.status).toBe("accepted");
    expect(setStatusMock).not.toHaveBeenCalled();
  });

  it("retries a lost response and checkpoints the journaled accept exactly once", async () => {
    adoptSaved(proj("local"), 3);
    acceptMock
      .mockRejectedValueOnce(new Error("network response lost"))
      .mockResolvedValueOnce({ ok: true, project: proj("server"), revision: 4, proposal: { id: "p1", status: "accepted" } });
    const past0 = past();
    await acceptProposal("proj", card());
    expect(acceptMock).toHaveBeenCalledTimes(2);
    expect(past()).toBe(past0 + 1);
    expect(useProjectStore.getState().revision).toBe(4);
    expect(useChatStore.getState().proposals.p1.status).toBe("accepted");
  });

  it("a double-click sends ONE atomic accept and the card ends accepted", async () => {
    adoptSaved(proj("local"), 3);
    const d = deferred<{ ok: true; project: ReturnType<typeof proj>; revision: number; proposal: { id: string; status: string } }>();
    acceptMock.mockReturnValueOnce(d.promise); // first click: accept stays pending
    acceptMock.mockResolvedValue({ ok: true, project: proj("server"), revision: 4, proposal: { id: "p1", status: "accepted" } }); // guard: unused

    const first = acceptProposal("proj", card()); // click 1 — registers in-flight, awaits PATCH
    const second = acceptProposal("proj", card()); // click 2 — in-flight guard returns immediately
    d.resolve({ ok: true, project: proj("server"), revision: 4, proposal: { id: "p1", status: "accepted" } });
    await Promise.all([first, second]);

    expect(acceptMock).toHaveBeenCalledTimes(1); // only ONE request left the client
    expect(useChatStore.getState().proposals.p1.status).toBe("accepted");
    expect(past()).toBe(1); // exactly one checkpoint
  });

  it("self-pends the expected revision until the accept response is applied", async () => {
    adoptSaved(proj("local"), 3);
    const d = deferred<{ ok: true; project: ReturnType<typeof proj>; revision: number; proposal: { id: string; status: string } }>();
    acceptMock.mockReturnValueOnce(d.promise);
    const pending = acceptProposal("proj", card());
    expect(isSelfPending(4)).toBe(true); // an SSE rev4 echo is ignored while the HTTP response is pending
    d.resolve({ ok: true, project: proj("server"), revision: 4, proposal: { id: "p1", status: "accepted" } });
    await pending;
    expect(isSelfPending(4)).toBe(false);
    expect(past()).toBe(1);
  });

  it("a double-click after the first accept LANDED does not re-accept (idempotent terminal guard)", async () => {
    adoptSaved(proj("local"), 3);
    acceptMock.mockResolvedValue({ ok: true, project: proj("server"), revision: 4, proposal: { id: "p1", status: "accepted" } });

    await acceptProposal("proj", card());
    expect(useChatStore.getState().proposals.p1.status).toBe("accepted");
    // A second click on the now-terminal card is a no-op — no second request, no revert.
    await acceptProposal("proj", card());
    expect(acceptMock).toHaveBeenCalledTimes(1);
    expect(useChatStore.getState().proposals.p1.status).toBe("accepted");
  });
});
