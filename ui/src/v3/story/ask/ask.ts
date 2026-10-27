// Pure helpers for the Story Ask bar: which provider a turn runs on, the Story context a
// turn carries, the suggestion chips, and the words pending proposals touch.

import { shotMode } from "motion-core";
import type { AgentPanelSettings, ChatConfig, TranscriptWord } from "../../../api";
import { activeProfile, profileChoices, settingsForProfile } from "../../../chat/agentConfiguration";
import type { ChatMsg, ErrorKind, ProposalCard } from "../../../chat/agentEvents";
import type { SceneSpan } from "../model";
import { sceneOfWord } from "../model";
import { orderedRange, type ProposalWordKind, type WordRange } from "../storyStore";

export type Readiness =
  | { state: "checking" }
  | { state: "ready"; label: string; settings: AgentPanelSettings }
  | { state: "offline"; reason: string; section?: "addons" };

// The selected profile when it can run, else the first one that can: the Ask bar never sends a turn
// to a provider the server would refuse.
export function askReadiness(
  config: ChatConfig | null,
  settings: AgentPanelSettings,
  configFailed: boolean,
  errorKind: ErrorKind | null,
  detail: string | null = null,
): Readiness {
  if ((errorKind === "assistant_not_installed" || errorKind === "auth") && detail) return { state: "offline",
    reason: detail, section: "addons" };
  if (configFailed) return { state: "offline", reason: "The co-pilot isn't reachable right now." };
  if (!config) return { state: "checking" };
  const selected = activeProfile(config, settings);
  const choice = selected?.profile.availability.available
    ? selected
  : profileChoices(config).find((c) => c.profile.availability.available) ?? null;
  if (!choice) {
    const error = selected?.profile.availability.error;
    if (error?.code === "authentication_checking") return { state: "checking" };
    return { state: "offline", reason: error?.message ?? "No AI provider is ready.",
      section: "addons" };
  }
  return { state: "ready", label: choice.providerLabel, settings: settingsForProfile(choice, settings) };
}

const CONTEXT_RE = /\n\n\[Story view · [^\]]*\]$/;

// What "this" means: the selection or the caret, and its scene with its picture (footage, cameo or
// animation: a character shows only in the last two), so a turn like "tighten this" is grounded
// in word indices the Story tools take. Shown to the model only; the sheet strips it.
export function turnContext(
  words: TranscriptWord[],
  spans: SceneSpan[],
  range: WordRange | null,
  caretWord: number | null,
): string {
  const scene = (i: number): string => {
    const s = sceneOfWord(spans, i)?.scene;
    return s ? ` in ${s.id} “${s.title}” (${shotMode(s.shot)})` : "";
  };
  if (range) {
    const { a, b } = orderedRange(range);
    const quote = words.slice(a, b + 1).map((w) => w.w).join(" ");
    const clipped = quote.length > 90 ? `${quote.slice(0, 89)}…` : quote;
    return `\n\n[Story view · selected words ${a}-${b} “${clipped}”${scene(a)}]`;
  }
  if (caretWord !== null && words[caretWord]) {
    return `\n\n[Story view · cursor at word ${caretWord} “${words[caretWord].w}”${scene(caretWord)}]`;
  }
  return "";
}

export function stripContext(text: string): string {
  return text.replace(CONTEXT_RE, "");
}

// Suggestion chips for a script-origin (animation) video are fixed; footage's are grounded on the
// selection once there is one.
export function suggestions(hasSelection: boolean, isAnimation: boolean): string[] {
  if (isAnimation) return ["Make your character excited", "Tighten this scene", "Make it vertical"];
  return hasSelection
    ? ["Tighten this", "Add B-roll here", "Make this an animation"]
  : ["Cut filler words", "Add B-roll here", "Add music"];
}

// The current exchange: the last user message and everything after it.
export function currentExchange(messages: ChatMsg[]): ChatMsg[] {
  let i = messages.length - 1;
  while (i > 0 && messages[i].role !== "user") i -= 1;
  return messages.slice(Math.max(0, i));
}

const TOOL_VERBS: Record<string, string> = {
  read_story: "Reading the story",
  read_transcript: "Reading the transcript",
  find_word: "Finding words",
  cut_fillers: "Finding cuts",
  plan_scenes: "Planning scenes",
  suggest_broll: "Picking B-roll",
  set_shot: "Directing shots",
  propose_patch: "Drafting an edit",
  propose_options: "Weighing options",
  get_project: "Reading the timeline",
  get_channel: "Reading the channel profile",
  read_notes: "Reading notes",
  read_preferences: "Reading preferences",
  append_note: "Saving a note",
};

export function toolLabel(name: string): string {
  const bare = name.replace(/^mcp__(editor|gpu)__/, "");
  return TOOL_VERBS[bare] ?? bare.replace(/_/g, " ");
}

export function pendingCards(proposals: Record<string, ProposalCard>): ProposalCard[] {
  return Object.values(proposals).filter((p) => p.status === "pending");
}

// Every word a pending proposal touches; a cut wins over a mark on the same word.
export function proposalWordMap(cards: ProposalCard[]): Map<number, ProposalWordKind> | null {
  const map = new Map<number, ProposalWordKind>();
  for (const card of cards) {
    for (const m of card.marks ?? []) {
      for (let i = m.a; i < m.b; i++) if (map.get(i) !== "cut") map.set(i, m.kind === "cut" ? "cut" : "mark");
    }
  }
  return map.size ? map : null;
}

export function changesLine(card: ProposalCard, fallback: string): string {
  const labels = (card.marks ?? []).map((m) => m.label);
  return labels.length ? labels.join(" · ") : fallback;
}
