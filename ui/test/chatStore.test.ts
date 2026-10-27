import { describe, it, expect, beforeEach } from "vitest";
import { useChatStore, ingestAgentEvent } from "../src/store/useChatStore";
import { initialChatState } from "../src/chat/agentEvents";
import { useProjectStore } from "../src/store/useProjectStore";

beforeEach(() => {
  useChatStore.setState(initialChatState());
  useProjectStore.setState({ project: null, revision: 0 });
  useProjectStore.temporal.getState().clear();
});

describe("useChatStore isolation from the project undo store", () => {
  it("ingestAgentEvent mutates only useChatStore — no project history entry", () => {
    const past0 = useProjectStore.temporal.getState().pastStates.length;

    ingestAgentEvent({ type: "chat_text", text: "hello" });
    ingestAgentEvent({ type: "tool_use", name: "mcp__editor__find_word", input: { phrase: "hook" } });
    ingestAgentEvent({ type: "proposal", id: "p1", title: "Cut", status: "pending" });

    expect(useChatStore.getState().messages).toEqual([{ role: "assistant", text: "hello" }]);
    expect(useChatStore.getState().proposals.p1.status).toBe("pending");
    // The project store's undo stack is untouched — chat never enters zundo.
    expect(useProjectStore.temporal.getState().pastStates.length).toBe(past0);
    expect(useProjectStore.getState().project).toBeNull();
  });
});

describe("appendUserMessage — arming + turn bookkeeping", () => {
  it.each(["authentication_required", "assistant_not_installed", "provider_unavailable"])("keeps the server refusal detail for %s", (code) => {
    const detail = "Verifying your key…";
    useChatStore.getState().failTurn(Object.assign(new Error(`409 Conflict: ${detail}`), { code, detail }));
    expect(useChatStore.getState().authError).toBe(detail);
  });
  it("arms auto-apply on an exact command and records the user turn", () => {
    useChatStore.getState().appendUserMessage("just do it");
    const s = useChatStore.getState();
    expect(s.messages).toEqual([{ role: "user", text: "just do it" }]);
    expect(s.turnActive).toBe(true);
    expect(s.autoApproveArmed).toBe(true);
  });

  it("disarms on any other message and clears prior tool chips + error", () => {
    useChatStore.setState({
      autoApproveArmed: true,
      toolChips: [{ name: "x" }],
      authError: "old error",
    });
    useChatStore.getState().appendUserMessage("cut the intro please");
    const s = useChatStore.getState();
    expect(s.autoApproveArmed).toBe(false);
    expect(s.toolChips).toEqual([]);
    expect(s.authError).toBeNull();
  });

  it("settles a failed local turn without duplicating the durable user message", () => {
    useChatStore.getState().appendUserMessage("first attempt");
    const beforeFailure = useChatStore.getState().messages;

    useChatStore.getState().failTurn();

    const s = useChatStore.getState();
    expect(s.messages).toEqual(beforeFailure);
    expect(s.messages.filter((message) => message.text === "first attempt")).toHaveLength(1);
    expect(s.turnActive).toBe(false);
    expect(s.autoApproveArmed).toBe(false);
    expect(s.armedTurnId).toBeNull();
    expect(s.errorKind).toBe("runtime");
    expect(s.authError).toBe("Message could not be sent. Check your connection and try again.");
  });
});
