import { create } from "zustand";
import { api, type AgentPanelSettings, type ChatConfig } from "../api";
import {
  applyAgentEvent,
  armsAutoApprove,
  initialChatState,
  mergeJobs,
  mergeProposals,
  reconcileFromLog,
  type AgentMsg,
  type ChatState,
} from "../chat/agentEvents";

// Provider/profile selection and its knobs persist across reloads as one global preference.
const SETTINGS_KEY = "cutgraph.copilot.settings";
const DEFAULT_SETTINGS: AgentPanelSettings = {
  providerId: "claude", profileId: "default", model: null, effort: null, mode: "default",
};

function loadSettings(): AgentPanelSettings {
  if (typeof localStorage === "undefined") return { ...DEFAULT_SETTINGS };
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<AgentPanelSettings>;
    return { ...DEFAULT_SETTINGS, ...parsed };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function saveSettings(s: AgentPanelSettings): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
  } catch {
    // storage disabled/full — the choice still applies for this session, it just won't persist
  }
}

function availability(config: ChatConfig | null): string {
  return JSON.stringify(config?.providers.map((p) => ({
    id: p.id, availability: p.availability,
    profiles: p.profiles.map((profile) => ({ id: profile.id, availability: profile.availability })),
  })));
}

// The chat store is STANDALONE (Toast pattern) — deliberately NOT part of useProjectStore, whose
// zundo partialize tracks {project,selection}. Chat has no undo semantics; folding it into the project
// store would pollute the human's undo history. SSE pushes agent events here via `ingestAgentEvent`
// without threading a React callback (imperative bus, like `toast()`).

interface ChatStore extends ChatState {
  settings: AgentPanelSettings;
  chatConfig: ChatConfig | null;
  ingestAgentEvent: (msg: AgentMsg) => void;
  appendUserMessage: (text: string) => void;
  failTurn: (error?: unknown) => void;
  hydrate: (entries: unknown[]) => void;
  hydrateProposals: (list: unknown[]) => void;
  hydrateJobs: (list: unknown[]) => void;
  setConnected: (connected: boolean) => void;
  setSetting: (patch: Partial<AgentPanelSettings>) => void;
  setChatConfig: (config: ChatConfig) => void;
  reset: () => void;
}

export const useChatStore = create<ChatStore>((set) => ({
  ...initialChatState(),
  settings: loadSettings(),
  chatConfig: null,

  ingestAgentEvent: (msg) => set((s) => applyAgentEvent(s, msg)),

  // A new outgoing turn: record it, open a FRESH turn (bump turnId), and arm/disarm auto-apply from the
  // exact-match heuristic — binding the arm to THIS turn (armedTurnId) so it can only auto-accept
  // a proposal that arrives in it. Clears the previous turn's tool chips; clears a transient
  // error but KEEPS a persistent auth banner (sending another message doesn't fix a rejected key).
  appendUserMessage: (text) =>
    set((s) => {
      const armed = armsAutoApprove(text);
      const turnId = s.turnId + 1;
      const keepAuth = s.errorKind === "auth";
      return {
        messages: [...s.messages, { role: "user", text }],
        turnActive: true,
        turnId,
        authError: keepAuth ? s.authError : null,
        errorKind: keepAuth ? s.errorKind : null,
        toolChips: [],
        autoApproveArmed: armed,
        armedTurnId: armed ? turnId : null,
      };
    }),

  // The POST can fail before the SSE stream emits agent_error. Keep the durable
  // user message, but settle this local turn with the same sanitized
  // error path so the composer can recover without inventing a receipt/event.
  failTurn: (error) => {
    const code = typeof error === "object" && error !== null && "code" in error ? error.code : null;
    const auth = code === "authentication_required";
    const missing = code === "assistant_not_installed";
    const detail = typeof error === "object" && error !== null && "detail" in error && typeof error.detail === "string"
      ? error.detail : null;
    set((s) => applyAgentEvent(s, {
      type: "agent_error",
      kind: missing ? "assistant_not_installed" : auth ? "auth" : "runtime",
      message: detail || "Message could not be sent. Check your connection and try again.",
    }));
  },

  // Reconnect reconcile: rebuild the transcript from the durable jsonl, preserving live terminal
  // proposal statuses (reconcileFromLog) so a just-applied accept survives the rebuild.
  hydrate: (entries) => set((s) => reconcileFromLog(s, entries)),

  // Reconnect reconcile: overlay the authoritative proposal-store status onto the log-rebuilt
  // cards (terminal-wins, so a durable pending never reverts a local terminal).
  hydrateProposals: (list) => set((s) => mergeProposals(s, list)),
  hydrateJobs: (list) => set((s) => mergeJobs(s, list)),

  setConnected: (connected) => set({ connected }),

  // Patch one knob, persist the whole set. The next turn carries it; if it differs from the running
  // session the server rebuilds. reset() (merge, not replace) leaves settings/chatConfig untouched.
  setSetting: (patch) =>
    set((s) => {
      const settings = { ...s.settings, ...patch };
      saveSettings(settings);
      return { settings };
    }),

  setChatConfig: (chatConfig) => set((s) => ({ chatConfig,
    ...((s.errorKind === "auth" || s.errorKind === "assistant_not_installed") && availability(s.chatConfig) !== availability(chatConfig)
      ? { errorKind: null, authError: null } : {}),
  })),

  reset: () => set(initialChatState()),
}));

// Imperative entry point for sse.ts (mirrors `toast()`), so the SSE dispatcher can push without React.
export function ingestAgentEvent(msg: AgentMsg): void {
  useChatStore.getState().ingestAgentEvent(msg);
}

export function setConnected(connected: boolean): void {
  useChatStore.getState().setConnected(connected);
}

// Reconnect reconcile: the SSE stream has no ids and does not replay, so on every
// reconnect the client rebuilds chat + proposals from the DURABLE stores — the SAME code path as the
// initial Ask bar mount load. Serialized (log THEN proposals) so the authoritative durable status
// overlays the log rebuild last. Idempotent: hydrate REPLACES the transcript from the deduped log
// tail and proposals merge by id, so a reconnect after a gap can neither duplicate nor drop a card.
export async function reconcileChat(project: string): Promise<void> {
  try {
    const log = await api.getChatLog(project);
    useChatStore.getState().hydrate(log.entries);
  } catch {
    // A transient fetch failure leaves the current transcript; the next reconnect retries.
  }
  try {
    const props = await api.getProposals(project);
    useChatStore.getState().hydrateProposals(props.proposals);
  } catch {
    // ignore — cards keep their current status until the next successful reconcile
  }
  try {
    const jobs = await api.getComputeJobs(project);
    useChatStore.getState().hydrateJobs(jobs.jobs);
  } catch {
    // Durable job state is retried on the next reconnect.
  }
}
