import type { ProposalCard } from "../../../chat/agentEvents";

// One pending co-pilot proposal: what it is, one line of why, what
// changes, and Accept / Reject. Accept goes through the co-pilot's accept path (one undo step).
export default function AskCard({
  card,
  changes,
  stale,
  busy,
  onAccept,
  onReject,
}: {
  card: ProposalCard;
  changes: string;
  stale: boolean;
  busy: boolean;
  onAccept: (card: ProposalCard) => void;
  onReject: (card: ProposalCard) => void;
}) {
  const saves = typeof card.savesSec === "number" && card.savesSec > 0 ? `saves ${card.savesSec.toFixed(1)}s` : null;
  // A Story tool's default rationale restates its changes; only a model-written "why" adds anything.
  const why = card.rationale && !(card.marks?.length && card.rationale.startsWith(card.marks[0].label)) ? card.rationale : null;
  return (
    <div className="proposal ask-card" role="group" aria-label={`Proposal: ${card.title}`} data-proposal={card.id}>
      <span className="spark" aria-hidden="true">✦</span>
      <div className="ask-card-body">
        <p className="ask-card-title">
          <b>{card.title}</b>
          {saves && <span className="ask-card-saves">{saves}</span>}
          {stale && <span className="ask-card-stale">Out of date · the video changed</span>}
        </p>
        {why && why !== changes && <p className="ask-card-why">{why}</p>}
        <p className="ask-card-changes" title={changes}>{changes}</p>
      </div>
      <button type="button" className="ghost" disabled={busy} onClick={() => onReject(card)}>
        Reject
      </button>
      <button
        type="button"
        className="primary"
        disabled={busy || stale}
        title={stale ? "Ask again: the video changed since." : undefined}
        onClick={() => onAccept(card)}
      >
        Accept
      </button>
    </div>
  );
}
