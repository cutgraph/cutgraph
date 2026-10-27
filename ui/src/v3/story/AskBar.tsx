import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api";
import { acceptProposal, maybeAutoAccept, rejectProposal } from "../../chat/acceptProposal";
import { initialChatState, pickAutoAccept, type ProposalCard } from "../../chat/agentEvents";
import { summarizeOps } from "../../chat/opSummary";
import { persistCurrent } from "../../persist";
import { reconcileChat, useChatStore } from "../../store/useChatStore";
import { undo, useProjectStore } from "../../store/useProjectStore";
import { linkProps } from "../router";
import { toast } from "../Toast";
import { isEditingText } from "./StoryView";
import { markProposalWords, orderedRange, setCaret, useStory } from "./storyStore";
import { useStoryModel } from "./useStoryModel";
import AskCard from "./ask/AskCard";
import AskSheet from "./ask/AskSheet";
import {
  askReadiness,
  changesLine,
  currentExchange,
  pendingCards,
  proposalWordMap,
  suggestions,
  turnContext,
} from "./ask/ask";
import "./ask/ask.css";

function undoAndSave(video: string): void {
  undo();
  void persistCurrent(video);
}

// The Story view's Ask bar on the co-pilot's store and transport. A turn carries
// the selection as context; the reply streams into a small sheet; each proposal is a card whose Accept
// is the accept path (server-guarded, one undo step) and whose words light up in the script.
export default function AskBar() {
  const video = useProjectStore((s) => s.project?.name ?? null);
  const doc = useProjectStore((s) => s.project);
  const revision = useProjectStore((s) => s.revision);
  const { words, spans } = useStoryModel();
  const range = useStory((s) => s.range);
  const caretWord = useStory((s) => s.caretWord);

  const messages = useChatStore((s) => s.messages);
  const toolChips = useChatStore((s) => s.toolChips);
  const proposals = useChatStore((s) => s.proposals);
  const turnActive = useChatStore((s) => s.turnActive);
  const authError = useChatStore((s) => s.authError);
  const errorKind = useChatStore((s) => s.errorKind);
  const autoApproveArmed = useChatStore((s) => s.autoApproveArmed);
  const armedTurnId = useChatStore((s) => s.armedTurnId);
  const settings = useChatStore((s) => s.settings);
  const chatConfig = useChatStore((s) => s.chatConfig);
  const setChatConfig = useChatStore((s) => s.setChatConfig);
  const appendUserMessage = useChatStore((s) => s.appendUserMessage);
  const failTurn = useChatStore((s) => s.failTurn);

  const [draft, setDraft] = useState("");
  const [sheetOpen, setSheetOpen] = useState(false);
  const [configFailed, setConfigFailed] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Each video starts from its own durable co-pilot history, never the previous video's cards.
  useEffect(() => {
    if (!video) return;
    useChatStore.setState({ ...initialChatState(), connected: useChatStore.getState().connected });
    void reconcileChat(video);
  }, [video]);

  useEffect(() => {
    if (chatConfig) return;
    let live = true;
    Promise.resolve()
      .then(() => api.getChatConfig())
      .then((config) => live && setChatConfig(config), () => live && setConfigFailed(true));
    return () => {
      live = false;
    };
  }, [chatConfig, setChatConfig]);

  const pending = useMemo(() => pendingCards(proposals), [proposals]);
  const wordMap = useMemo(() => proposalWordMap(pending), [pending]);
  useEffect(() => markProposalWords(wordMap), [wordMap]);
  useEffect(() => () => markProposalWords(null), []);

  useEffect(() => {
    if (turnActive) setSheetOpen(true);
  }, [turnActive]);

  // "just do it" arms the next proposal of that turn, exactly as in the chat panel.
  useEffect(() => {
    const card = pickAutoAccept(useChatStore.getState());
    if (card && video) void maybeAutoAccept(video, card);
  }, [proposals, autoApproveArmed, armedTurnId, video]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "/" && !isEditingText(e.target)) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const readiness = askReadiness(chatConfig, settings, configFailed, errorKind, authError);
  const ready = readiness.state === "ready";
  useEffect(() => {
    if (ready || !chatConfig) return;
    let live = true;
    const timer = window.setInterval(() => {
      void api.getChatConfig().then((config) => live && setChatConfig(config), () => {});
    }, 1000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [ready, chatConfig, setChatConfig]);
  const exchange = useMemo(() => currentExchange(messages), [messages]);

  async function send(text: string): Promise<void> {
    const t = text.trim();
    if (!t || !video || turnActive || readiness.state !== "ready") return;
    const context = turnContext(words, spans, range, caretWord);
    appendUserMessage(t);
    setDraft("");
    setSheetOpen(true);
    // The selection now lives in the turn; the proposal's own marks take over the script highlight.
    if (range) setCaret(orderedRange(range).a);
    try {
      await api.sendChat(video, t + context, useChatStore.getState().autoApproveArmed, readiness.settings);
    } catch (error) {
      failTurn(error);
    }
  }

  async function accept(card: ProposalCard): Promise<void> {
    if (!video) return;
    setBusyId(card.id);
    try {
      await acceptProposal(video, card);
    } finally {
      setBusyId(null);
    }
    if (useChatStore.getState().proposals[card.id]?.status === "accepted") {
      toast(`Applied · ${card.title}`, { label: "Undo", run: () => undoAndSave(video) });
    }
  }

  function reject(card: ProposalCard): void {
    if (video) void rejectProposal(video, card, "Rejected in the Story view");
  }

  const runtimeError = errorKind && errorKind !== "auth" ? authError : null;
  const placeholder = turnActive ? "The Assistant is working…" : "Ask the Assistant…";

  return (
    <footer className="ask" data-slot="ask">
      <small className="muted">Cutgraph Assistant · Powered by Claude</small>
      {(exchange.length > 0 || turnActive) && (
        <AskSheet
          exchange={exchange}
          chips={toolChips}
          working={turnActive}
          error={runtimeError}
          open={sheetOpen}
          onToggle={() => setSheetOpen((o) => !o)}
        />
      )}
      {pending.length > 0 && (
        <div className="ask-cards">
          {pending.map((card) => (
            <AskCard
              key={card.id}
              card={card}
              changes={changesLine(card, summarizeOps(card.ops ?? [], doc, doc?.fps ?? 30))}
              stale={typeof card.baseRevision === "number" && card.baseRevision !== revision}
              busy={busyId !== null}
              onAccept={(c) => void accept(c)}
              onReject={reject}
            />
          ))}
        </div>
      )}
      {readiness.state === "offline" && (
        <div className="ask-offline" role="status">
          <p>
            <b>{readiness.reason}</b>
          </p>
          <a className="ghost" {...linkProps({ view: "settings", section: readiness.section })}>
            {readiness.section === "addons" ? "Open Settings, Add-ons" : "Open Settings"}
          </a>
        </div>
      )}
      <form
        className="askform"
        autoComplete="off"
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
      >
        <span className="spark" aria-hidden="true">✦</span>
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && e.currentTarget.blur()}
          placeholder={placeholder}
          aria-label="Ask the Assistant"
          disabled={!ready || turnActive}
        />
        {ready && (
          <div className="sugs">
            {suggestions(range !== null, doc?.narration != null).map((s) => (
              <button key={s} type="button" disabled={turnActive} onClick={() => void send(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
      </form>
    </footer>
  );
}
