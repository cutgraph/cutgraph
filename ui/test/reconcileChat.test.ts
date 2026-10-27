import { describe, it, expect, vi, beforeEach } from "vitest";

// reconcileChat is the reconnect (and initial-load) reconcile. It must SERIALIZE the two durable
// fetches (log THEN proposals) so the authoritative durable status always overlays the log rebuild last
// — closing the race where getProposals-first-then-getChatLog reverted an accepted card to pending.

const { getChatLogMock, getProposalsMock } = vi.hoisted(() => ({
  getChatLogMock: vi.fn(),
  getProposalsMock: vi.fn(),
}));
vi.mock("../src/api", () => ({ api: { getChatLog: getChatLogMock, getProposals: getProposalsMock } }));

import { reconcileChat, useChatStore } from "../src/store/useChatStore";
import { initialChatState } from "../src/chat/agentEvents";

beforeEach(() => {
  useChatStore.setState(initialChatState());
  getChatLogMock.mockReset();
  getProposalsMock.mockReset();
});

describe("reconcileChat — serialized log→proposals reconcile", () => {
  it("the durable authoritative status overlays the log rebuild", async () => {
    getChatLogMock.mockResolvedValue({
      entries: [
        { role: "user", text: "tighten" },
        { role: "agent", type: "proposal", id: "p1", title: "Cut", status: "pending" },
      ],
    });
    // The proposal file says it was accepted since creation — this MUST win over the log's pending.
    getProposalsMock.mockResolvedValue({ proposals: [{ id: "p1", title: "Cut", status: "accepted", savesSec: 6 }] });

    await reconcileChat("proj");

    const s = useChatStore.getState();
    expect(s.messages).toEqual([{ role: "user", text: "tighten" }]);
    expect(s.proposals.p1.status).toBe("accepted");
    expect(s.proposals.p1.savesSec).toBe(6);
  });

  it("a proposals-fetch failure leaves the log-rebuilt transcript intact (best-effort)", async () => {
    getChatLogMock.mockResolvedValue({ entries: [{ role: "user", text: "hi" }] });
    getProposalsMock.mockRejectedValue(new Error("500 Internal Server Error"));

    await reconcileChat("proj");

    expect(useChatStore.getState().messages).toEqual([{ role: "user", text: "hi" }]);
  });

  it("a locally-accepted card is not reverted when the durable fetch still lags at pending", async () => {
    // Live state: p1 was just accepted locally; its status write hasn't landed on disk yet.
    useChatStore.getState().ingestAgentEvent({ type: "proposal", id: "p1", title: "Cut", status: "pending", ops: [] });
    useChatStore.getState().ingestAgentEvent({ type: "proposal", id: "p1", status: "accepted" });
    getChatLogMock.mockResolvedValue({
      entries: [{ role: "agent", type: "proposal", id: "p1", title: "Cut", status: "pending" }],
    });
    getProposalsMock.mockResolvedValue({ proposals: [{ id: "p1", title: "Cut", status: "pending" }] });

    await reconcileChat("proj");

    expect(useChatStore.getState().proposals.p1.status).toBe("accepted"); // never reverted
  });
});
