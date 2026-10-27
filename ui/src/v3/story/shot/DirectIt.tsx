// "Direct it" in the Shot view: a direction in plain words for this scene's shot. It is an Ask
// bar turn on the same co-pilot and transport, scoped by its Story context to this one scene and the
// set_shot tool, so what comes back is a proposal card the user accepts exactly as in the Ask bar.
import { useMemo, useState } from "react";
import type { ShotMode } from "motion-core";
import { api } from "../../../api";
import { acceptProposal, rejectProposal } from "../../../chat/acceptProposal";
import type { ChatMsg, ProposalCard } from "../../../chat/agentEvents";
import { summarizeOps } from "../../../chat/opSummary";
import { useChatStore } from "../../../store/useChatStore";
import { useProjectStore } from "../../../store/useProjectStore";
import { toast } from "../../Toast";
import AskCard from "../ask/AskCard";
import { askReadiness, changesLine, currentExchange, pendingCards } from "../ask/ask";
import { undoStep } from "../history";
import { AddonSettingsLink } from "../../Addons";

/**
 * The Story context that scopes a turn to one scene's shot (the Ask sheet strips it like any other),
 * naming its picture: footage shows no character, so a look asked for there needs cameo or animation.
 */
export function directContext(n: number, sceneId: string, title: string, mode: ShotMode): string {
  const picture = mode === "footage" ? "footage, which shows no character: set mode cameo or animation to show one" : mode;
  return `\n\n[Story view · Shot view of scene ${n}, ${sceneId} “${title.replace(/[\]\n]/g, " ")}” (${picture}) · direct only this scene's shot, with set_shot]`;
}

type Op = { op?: unknown; path?: unknown; value?: unknown };

/** Pending proposals that change this scene's shot: a guard on the scene's id and an op on its shot. */
export function sceneShotCards(cards: ProposalCard[], sceneId: string): ProposalCard[] {
  return cards.filter((card) => {
    const ops = (card.ops ?? []) as Op[];
    const guard = ops.find((o) => o.op === "test" && o.value === sceneId && typeof o.path === "string" && /^\/scenes\/\d+\/id$/.test(o.path));
    return !!guard && ops.some((o) => o.path === (guard.path as string).replace(/id$/, "shot"));
  });
}

function lastReply(messages: ChatMsg[]): string | null {
  const replies = currentExchange(messages).filter((m) => m.role === "assistant");
  return replies.length ? replies[replies.length - 1].text : null;
}

export default function DirectIt({ video, sceneId, n, title, mode }: { video: string; sceneId: string; n: number; title: string; mode: ShotMode }) {
  const doc = useProjectStore((s) => s.project);
  const revision = useProjectStore((s) => s.revision);
  const chatConfig = useChatStore((s) => s.chatConfig);
  const settings = useChatStore((s) => s.settings);
  const errorKind = useChatStore((s) => s.errorKind);
  const authError = useChatStore((s) => s.authError);
  const turnActive = useChatStore((s) => s.turnActive);
  const turnId = useChatStore((s) => s.turnId);
  const messages = useChatStore((s) => s.messages);
  const proposals = useChatStore((s) => s.proposals);
  const [draft, setDraft] = useState("");
  const [turn, setTurn] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const readiness = askReadiness(chatConfig, settings, false, errorKind, authError);
  const cards = useMemo(() => sceneShotCards(pendingCards(proposals), sceneId), [proposals, sceneId]);
  const mine = turn !== null && turn === turnId;
  const reply = mine && !turnActive && !cards.length ? (errorKind ? authError : lastReply(messages)) : null;

  async function send(): Promise<void> {
    const text = draft.trim();
    if (!text || turnActive || readiness.state !== "ready") return;
    const chat = useChatStore.getState();
    chat.appendUserMessage(text);
    setTurn(useChatStore.getState().turnId);
    setDraft("");
    try {
      await api.sendChat(video, text + directContext(n, sceneId, title, mode), useChatStore.getState().autoApproveArmed, readiness.settings);
    } catch (error) {
      useChatStore.getState().failTurn(error);
    }
  }

  async function accept(card: ProposalCard): Promise<void> {
    setBusy(true);
    try {
      await acceptProposal(video, card);
    } finally {
      setBusy(false);
    }
    if (useChatStore.getState().proposals[card.id]?.status === "accepted") toast(`Applied · ${card.title}`, { label: "Undo", run: undoStep });
  }

  const placeholder = readiness.state === "offline"
    ? "Direct it: connect an AI provider in Settings"
    : turnActive ? "The Assistant is working…" : "Direct it: “more excited, wave hello”";

  return (
    <div className="direct-it" data-slot="direct">
      {readiness.state === "offline" && <div className="addon-notice" role="status">
        <p>{readiness.reason}</p><AddonSettingsLink />
      </div>}
      <form className="direct" autoComplete="off" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <span className="spark" aria-hidden="true">✦</span>
        <input value={draft} placeholder={placeholder} aria-label="Direct the shot" disabled={readiness.state !== "ready" || turnActive}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape" && draft) { e.stopPropagation(); setDraft(""); } }} />
      </form>
      {mine && turnActive && <p className="direct-status" role="status" aria-label="Direct it"><i className="ask-dot" aria-hidden="true" /> The Assistant is directing this shot…</p>}
      {reply && <p className="direct-status" role="status" aria-label="Direct it">{reply}</p>}
      {cards.length > 0 && (
        <div className="direct-cards">
          {cards.map((card) => (
            <AskCard key={card.id} card={card} changes={changesLine(card, summarizeOps(card.ops ?? [], doc, doc?.fps ?? 30))}
              stale={typeof card.baseRevision === "number" && card.baseRevision !== revision} busy={busy}
              onAccept={(c) => void accept(c)} onReject={(c) => void rejectProposal(video, c, "Rejected in the Shot view")} />
          ))}
        </div>
      )}
    </div>
  );
}
