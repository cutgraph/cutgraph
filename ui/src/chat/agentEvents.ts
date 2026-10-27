// Pure chat reducer. No React, no zustand — node-testable. The SSE dispatcher and the
// chat store both funnel agent events through `applyAgentEvent`; `hydrateFromLog` replays the durable
// jsonl on reconnect through the SAME reducer so streamed and reconciled state can never diverge.

export type ChatRole = "user" | "assistant" | "system";

// agent_error carries a server-classified kind (agent_host._classify_assistant_error / _classify_error):
// "auth" → the persistent Settings connection banner; others show the raw message.
export type ErrorKind = "auth" | "billing" | "runtime" | "assistant_not_installed";

export interface ChatMsg {
  role: ChatRole;
  text: string;
}

export interface ToolChip {
  name: string;
  input?: unknown;
}

export interface ProposalCard {
  id: string;
  title: string;
  rationale?: string;
  savesSec?: number | null;
  status: string;
  ops?: unknown[];
  intentGroups?: { id: string; intent: string; ops: unknown[] }[];
  citations?: { kind: "transcript" | "visual"; start: number; end: number; summary: string; evidenceId?: string }[];
  preview?: { startF: number; endF: number; previewRef: string };
  /** Revision of the authoritative project document this patch was created against. */
  baseRevision?: number;
  /** Story tools: the transcript words this proposal touches, [a, b) by flat wordIdx. */
  marks?: ProposalMark[];
  // The chat turn this card was CREATED in (client-derived — the server does not tag events). The
  // "just do it" arm binds to a turn and auto-accepts ONLY a card whose turnId matches: this is
  // what stops a stale arm from grabbing an older pending card or a later Pick-triggered proposal.
  turnId?: number;
}

export interface ProposalMark {
  a: number;
  b: number;
  kind: "cut" | "mark";
  label: string;
}

export interface OptionsGallery {
  kind: string;
  options: { label: string; description?: string; previewRef?: string }[];
  resolved: string | null;
}

export type JobChip = ComputeJob;

export interface ChatState {
  connected: boolean;
  authError: string | null;
  // Classification of the current agent_error (drives the persistent auth banner). null when no error.
  errorKind: ErrorKind | null;
  messages: ChatMsg[];
  turnActive: boolean;
  // Monotonic client-derived turn counter. A turn opens on a user send (appendUserMessage) or on the
  // first agent event of a server-synthesized turn (Pick/choose) and closes on turn_done/agent_error.
  turnId: number;
  toolChips: ToolChip[];
  proposals: Record<string, ProposalCard>;
  options: OptionsGallery | null;
  jobs: Record<string, JobChip>;
  autoApproveArmed: boolean;
  // The turn the "just do it" arm is bound to. null when disarmed. An auto-accept fires only
  // for a pending card whose turnId === armedTurnId.
  armedTurnId: number | null;
}

// One SSE agent event. The wire shape is `{type, ...data}` (studio flattens AgentEvent.data), so extra
// keys are read positionally per case.
export interface AgentMsg {
  type: string;
  [key: string]: unknown;
}

export function initialChatState(): ChatState {
  return {
    connected: false,
    authError: null,
    errorKind: null,
    messages: [],
    turnActive: false,
    turnId: 0,
    toolChips: [],
    proposals: {},
    options: null,
    jobs: {},
    autoApproveArmed: false,
    armedTurnId: null,
  };
}

// exact-match arm — arms ONLY the next proposal, disarms on any other message. Kept pure here
// so both the store and its test agree on the trigger.
const ARM_RE = /^(just do it|go ahead|apply it)[.!]?$/i;

export function armsAutoApprove(text: string): boolean {
  return ARM_RE.test(text.trim());
}

const _TERMINAL = new Set(["accepted", "rejected", "stale"]);

// A proposal in a terminal state is settled — it can never re-open. Terminal ALWAYS wins over pending
// during any reconcile: a just-applied accept must never be reverted to a live card.
export function isTerminalStatus(status: string): boolean {
  return _TERMINAL.has(status);
}

// A turn opens on the FIRST turn-bearing agent event when none is active (bumping the counter), or stays
// on the current turn otherwise. Used by chat_text/tool_use and a creation `proposal` so a server-
// synthesized turn (a Pick with no preceding user send) still gets a fresh turnId.
function openTurn(state: ChatState): { turnActive: true; turnId: number } {
  return state.turnActive
    ? { turnActive: true, turnId: state.turnId }
  : { turnActive: true, turnId: state.turnId + 1 };
}

function normalizeKind(kind: unknown): ErrorKind {
  return kind === "auth" || kind === "billing" || kind === "assistant_not_installed" ? kind : "runtime";
}

export function applyAgentEvent(state: ChatState, msg: AgentMsg): ChatState {
  switch (msg.type) {
    case "chat_text":
      // The agent produced text ⇒ the session is authenticated: clear any auth banner.
      return {
        ...state,
        ...openTurn(state),
        messages: [...state.messages, { role: "assistant", text: String(msg.text ?? "") }],
        authError: null,
        errorKind: null,
      };

    case "tool_use":
      return {
        ...state,
        ...openTurn(state),
        toolChips: [...state.toolChips, { name: String(msg.name ?? ""), input: msg.input }],
      };

    case "proposal": {
      const id = String(msg.id ?? "");
      if (!id) return state;
      const prev = state.proposals[id];
      // A creation event carries title/ops; a status-only flip (accepted/stale echo) carries neither and
      // must NOT open a turn or re-tag the card's turnId (else a status echo would spuriously start a turn).
      const isCreation = msg.ops !== undefined || msg.title !== undefined;
      const turn = isCreation ? openTurn(state) : null;
      const card: ProposalCard = {
        id,
        title: String(msg.title ?? prev?.title ?? ""),
        rationale: (msg.rationale as string | undefined) ?? prev?.rationale,
        savesSec: (msg.savesSec as number | null | undefined) ?? prev?.savesSec ?? null,
        status: String(msg.status ?? prev?.status ?? "pending"),
        ops: (msg.ops as unknown[] | undefined) ?? prev?.ops,
        intentGroups: (msg.intentGroups as ProposalCard["intentGroups"] | undefined) ?? prev?.intentGroups,
        citations: (msg.citations as ProposalCard["citations"] | undefined) ?? prev?.citations,
        preview: (msg.preview as ProposalCard["preview"] | undefined) ?? prev?.preview,
        baseRevision: (msg.baseRevision as number | undefined) ?? prev?.baseRevision,
        marks: (msg.marks as ProposalMark[] | undefined) ?? prev?.marks,
        turnId: isCreation ? turn!.turnId : prev?.turnId,
      };
      return { ...state, ...(turn ?? {}), proposals: { ...state.proposals, [id]: card } };
    }

    case "options": {
      // A resolved event (`{kind, resolved:<label>}`) marks the ACTIVE gallery picked and keeps its
      // cards; a fresh gallery event (`{kind, options:[...]}`) replaces it. A resolved event with no
      // live gallery is a no-op (nothing to grey).
      if (typeof msg.resolved === "string") {
        if (!state.options) return state;
        return { ...state, options: { ...state.options, resolved: msg.resolved } };
      }
      return {
        ...state,
        options: {
          kind: String(msg.kind ?? ""),
          options: (msg.options as OptionsGallery["options"] | undefined) ?? [],
          resolved: null,
        },
      };
    }

    case "job": {
      const id = String(msg.id ?? "");
      if (!id) return state;
      const prev = state.jobs[id];
      const incomingSequence = typeof msg.sequence === "number" ? msg.sequence : undefined;
      const authoritativeSnapshot = msg.__authoritativeSnapshot === true;
      const equalSequence = prev?.sequence !== undefined && incomingSequence === prev.sequence;
      if (prev?.sequence !== undefined && (
        (incomingSequence === undefined && !authoritativeSnapshot)
        || (incomingSequence !== undefined && incomingSequence < prev.sequence)
      )) {
        return state;
      }
      // A GET /compute/jobs response may carry the same event sequence as the SSE event that
      // arrived first. Merge its durable metadata without allowing an equal-sequence response to
      // regress the already-visible operational fields.
      if (equalSequence) {
        const merged = { ...prev };
        if (authoritativeSnapshot) {
          if (typeof msg.kind === "string") merged.kind = msg.kind;
          if (typeof msg.status === "string") merged.status = msg.status;
          if (typeof msg.progress === "number") merged.progress = msg.progress;
          if (typeof msg.progressStage === "string") merged.progressStage = msg.progressStage;
          if (typeof msg.canCancel === "boolean") merged.canCancel = msg.canCancel;
          if (typeof msg.canRetry === "boolean") merged.canRetry = msg.canRetry;
          if (typeof msg.error === "string") merged.error = msg.error;
          if (typeof msg.errorCode === "string") merged.errorCode = msg.errorCode;
          if (typeof msg.output === "string") merged.output = msg.output;
        }
        if (typeof msg.createdAt === "string") merged.createdAt = msg.createdAt;
        if (typeof msg.createdOrdinal === "number") merged.createdOrdinal = msg.createdOrdinal;
        if (typeof msg.startedAt === "string") merged.startedAt = msg.startedAt;
        if (typeof msg.completedAt === "string") merged.completedAt = msg.completedAt;
        if (msg.attemptState === "retrying") {
          merged.attemptState = msg.attemptState;
        } else if (msg.attemptState === null || authoritativeSnapshot) {
          merged.attemptState = undefined;
        }
        if (typeof msg.requiresApproval === "boolean") merged.requiresApproval = msg.requiresApproval;
        else if (typeof msg.status === "string" && msg.status !== "needs-approval") merged.requiresApproval = false;
        if (msg.retryState === "retrying") merged.retryState = msg.retryState;
        if (typeof msg.retryChildJobId === "string") merged.retryChildJobId = msg.retryChildJobId;
        return { ...state, jobs: { ...state.jobs, [id]: merged } };
      }
      return {
        ...state,
        jobs: {
          ...state.jobs,
          [id]: {
            id,
            kind: String(msg.kind ?? prev?.kind ?? ""),
            status: String(msg.status ?? prev?.status ?? ""),
            createdAt: typeof msg.createdAt === "string" ? msg.createdAt : prev?.createdAt,
            createdOrdinal: typeof msg.createdOrdinal === "number" ? msg.createdOrdinal : prev?.createdOrdinal,
            startedAt: typeof msg.startedAt === "string" ? msg.startedAt : prev?.startedAt,
            completedAt: typeof msg.completedAt === "string" ? msg.completedAt : prev?.completedAt,
            attemptState: msg.attemptState === "retrying"
              ? msg.attemptState
            : msg.attemptState === null
                ? undefined
            : authoritativeSnapshot
                ? undefined
            : prev?.attemptState,
            requiresApproval: typeof msg.requiresApproval === "boolean"
              ? msg.requiresApproval
            : typeof msg.status === "string" && msg.status !== "needs-approval"
                ? false
            : prev?.requiresApproval,
            progress: typeof msg.progress === "number" ? msg.progress : prev?.progress ?? 0,
            progressStage: String(msg.progressStage ?? prev?.progressStage ?? "Pending"),
            sequence: incomingSequence ?? prev?.sequence,
            canCancel: typeof msg.canCancel === "boolean" ? msg.canCancel : prev?.canCancel ?? false,
            canRetry: typeof msg.canRetry === "boolean" ? msg.canRetry : prev?.canRetry ?? false,
            error: typeof msg.error === "string" ? msg.error : prev?.error,
            errorCode: typeof msg.errorCode === "string" ? msg.errorCode : prev?.errorCode,
            output: typeof msg.output === "string" ? msg.output : prev?.output,
            parentJobId: typeof msg.parentJobId === "string" ? msg.parentJobId : prev?.parentJobId,
            retryState: msg.retryState === "retrying"
              ? msg.retryState
            : prev?.retryState,
            retryChildJobId: typeof msg.retryChildJobId === "string" ? msg.retryChildJobId : prev?.retryChildJobId,
          },
        },
      };
    }

    case "agent_error":
      // The turn died before any proposal could land ⇒ disarm the auto-accept: a "just do it"
      // arm must never outlive its turn and sweep up a later proposal.
      return {
        ...state,
        authError: String(msg.message ?? "agent error"),
        errorKind: normalizeKind(msg.kind),
        turnActive: false,
        autoApproveArmed: false,
        armedTurnId: null,
      };

    case "turn_done":
      // the arm binds to exactly one turn — disarm at turn end so a proposal-less armed turn
      // can't leave the arm live to grab an unrelated later proposal.
      return { ...state, turnActive: false, autoApproveArmed: false, armedTurnId: null };

    default:
      return state;
  }
}

// The proposal the "just do it" arm should auto-accept: pending, created in the SAME turn the arm bound
// to, and (implicitly) not yet terminal. Returns null when disarmed. Pure so the arm decision is
// node-testable without rendering the Ask bar (the effect there just calls this + maybeAutoAccept).
export function mergeJobs(state: ChatState, list: unknown[]): ChatState {
  let next = state;
  for (const raw of list) {
    if (!raw || typeof raw !== "object") continue;
    next = applyAgentEvent(next, {
      type: "job",
      ...(raw as Record<string, unknown>),
      __authoritativeSnapshot: true,
    });
  }
  return next;
}

export function pickAutoAccept(state: ChatState): ProposalCard | null {
  if (!state.autoApproveArmed || state.armedTurnId === null) return null;
  const turn = state.armedTurnId;
  return (
    Object.values(state.proposals).find((p) => p.status === "pending" && p.turnId === turn) ?? null
  );
}

// Overlay the durable proposal store (GET /api/chat/<p>/proposals) onto chat state on reconnect. The
// jsonl replays a proposal at its CREATION status only (later SSE status flips are ephemeral); the proposal files carry the authoritative status, so this merge is the source of truth for a
// card's accepted/rejected/stale state after a reconnect.
export function mergeProposals(state: ChatState, list: unknown[]): ChatState {
  const proposals = { ...state.proposals };
  for (const raw of list ?? []) {
    const p = raw as Partial<ProposalCard> & { savesSec?: number | null };
    if (!p || typeof p.id !== "string") continue;
    const prev = proposals[p.id];
    const incoming = String(p.status ?? prev?.status ?? "pending");
    // terminal ALWAYS wins over pending. The durable file is authoritative among terminals;
    // a LOCAL terminal (a just-applied accept whose status write may still be in flight) is never
    // reverted to the durable pending. Only when neither side is terminal does the durable status win.
    const status = isTerminalStatus(incoming)
      ? incoming
    : prev && isTerminalStatus(prev.status)
        ? prev.status
      : incoming;
    proposals[p.id] = {
      id: p.id,
      title: String(p.title ?? prev?.title ?? ""),
      rationale: (p.rationale as string | undefined) ?? prev?.rationale,
      savesSec: p.savesSec ?? prev?.savesSec ?? null,
      status,
      ops: (p.ops as unknown[] | undefined) ?? prev?.ops,
      intentGroups: (p.intentGroups as ProposalCard["intentGroups"] | undefined) ?? prev?.intentGroups,
      citations: (p.citations as ProposalCard["citations"] | undefined) ?? prev?.citations,
      preview: (p.preview as ProposalCard["preview"] | undefined) ?? prev?.preview,
      baseRevision: (typeof p.baseRevision === "number" ? p.baseRevision : prev?.baseRevision),
      marks: (p.marks as ProposalMark[] | undefined) ?? prev?.marks,
      turnId: prev?.turnId,
    };
  }
  return { ...state, proposals };
}

// Reconnect rebuild of the whole transcript from the durable jsonl, through the SAME
// reducer as the stream. It REPLACES messages (dedup is inherent — the log tail is the deduped source),
// but PRESERVES any proposal already terminal in the live state so a reconnect can never revert a
// just-applied accept to pending, and keeps a live card the log tail hasn't got yet. Serialized
// AHEAD of mergeProposals in reconcileChat so the authoritative durable status overlays last.
export function reconcileFromLog(prev: ChatState, entries: unknown[]): ChatState {
  const rebuilt = hydrateFromLog(entries);
  const proposals = { ...rebuilt.proposals };
  for (const [id, card] of Object.entries(prev.proposals)) {
    const fromLog = proposals[id];
    if (isTerminalStatus(card.status)) {
      proposals[id] = { ...(fromLog ?? card), status: card.status, turnId: fromLog?.turnId ?? card.turnId };
    } else if (!fromLog) {
      proposals[id] = card;
    }
  }
  return { ...rebuilt, proposals, connected: prev.connected };
}

// Rebuild chat state from the durable jsonl tail (GET /api/chat/<p>/log). User turns are `{role:"user"}`;
// agent turns carry a `type` and go through the reducer. Never marks a turn active (history is settled).
export function hydrateFromLog(entries: unknown[]): ChatState {
  let state = initialChatState();
  for (const raw of entries) {
    const e = raw as { role?: string; text?: string; type?: string };
    if (e.role === "user") {
      state = { ...state, messages: [...state.messages, { role: "user", text: String(e.text ?? "") }] };
    } else if (typeof e.type === "string") {
      state = applyAgentEvent(state, e as AgentMsg);
    }
  }
  return { ...state, turnActive: false };
}
import type { ComputeJob } from "../api";
