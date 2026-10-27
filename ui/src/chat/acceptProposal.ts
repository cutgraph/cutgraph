// The ONE accept/reject code path for a Claude proposal.
// The server atomically guards the receipt/revision, applies the RFC-6902 patch, and settles status.
// A failed accept means the edit NEVER applied → the card is `stale` (never a silent misapply).
// An in-flight guard makes a double-click / double auto-fire a single accept request.

import { api } from "../api";
import { useProjectStore, applyLocal, applyRemote } from "../store/useProjectStore";
import { useChatStore } from "../store/useChatStore";
import { toast } from "../toast";
import { markSelfPending, clearSelfPending } from "../selfPending";
import type { ProposalCard } from "./agentEvents";

// Cards whose accept request is outstanding. A second click / auto-fire returns before sending a second
// request that could race the first terminal transition.
const inFlight = new Set<string>();

export async function acceptProposal(
  projectName: string,
  card: ProposalCard,
  auto = false,
): Promise<void> {
  const current = useChatStore.getState().proposals[card.id];
  // Idempotency + in-flight guard: never re-accept a card that is already terminal (a
  // double-click after the first landed, or an SSE `accepted` echo) and never double-fire mid-request.
  if ((current && current.status !== "pending") || inFlight.has(card.id)) return;
  inFlight.add(card.id);

  const reason = auto ? "auto-applied on user command" : undefined;
  const expectedRevision = useProjectStore.getState().revision + 1;
  markSelfPending(expectedRevision);
  try {
    let res: Awaited<ReturnType<typeof api.acceptProposal>>;
    try {
      res = await api.acceptProposal(projectName, card.id, reason);
    } catch {
      // Retry the same idempotent journaled accept before deciding whether the failure was stale or
      // ambiguous. A lost response or post-write side-effect failure should never become stale UI.
      try {
        res = await api.acceptProposal(projectName, card.id, reason);
      } catch {
        const outcome = await reconcileAmbiguous(projectName, card, expectedRevision);
        if (outcome === "stale") await markStale(projectName, card);
        else if (outcome === "unknown" || outcome === "pending") {
          toast("Claude's accept is still being reconciled — refresh before retrying.");
        }
        return;
      }
    }
    applyAcceptedResult(card, res);
  } finally {
    clearSelfPending(expectedRevision);
    inFlight.delete(card.id);
  }
}

function applyAcceptedResult(
  card: ProposalCard,
  res: Awaited<ReturnType<typeof api.acceptProposal>>,
): void {
  // The atomic accept committed: the edit IS applied. The revision self-pending guard prevents the
  // server's pre-response SSE echo from creating a competing remote checkpoint. If reconciliation
  // observes that SSE already adopted this revision, do not checkpoint the same project twice.
  if (res.revision > useProjectStore.getState().revision) {
    applyLocal(() => ({ project: res.project }), { origin: "agent", label: `Claude: ${card.title}` });
    useProjectStore.setState({ revision: res.revision });
  }
  useChatStore.getState().ingestAgentEvent({ type: "proposal", id: card.id, status: "accepted" });
}

type StoredProposal = { id?: string; status?: string; acceptance?: { targetRevision?: number } };

async function reconcileAmbiguous(
  projectName: string,
  card: ProposalCard,
  expectedRevision: number,
): Promise<"accepted" | "stale" | "pending" | "unknown"> {
  try {
    const [{ project, revision }, archive] = await Promise.all([
      api.getProject(projectName),
      api.getProposals(projectName),
    ]);
    const proposal = (archive.proposals as StoredProposal[]).find((item) => item.id === card.id);
    const journalApplied = proposal?.acceptance?.targetRevision === revision && revision === expectedRevision;
    if (proposal?.status === "accepted" || journalApplied) {
      if (revision === expectedRevision) applyAcceptedResult(card, { ok: true, project, revision, proposal });
      else if (revision > useProjectStore.getState().revision) applyRemote(project, revision);
      useChatStore.getState().ingestAgentEvent({ type: "proposal", id: card.id, status: "accepted" });
      return "accepted";
    }
    if (revision > useProjectStore.getState().revision) applyRemote(project, revision);
    if (proposal?.status === "stale") return "stale";
    return "pending";
  } catch {
    return "unknown";
  }
}

async function markStale(projectName: string, card: ProposalCard): Promise<void> {
  useChatStore.getState().ingestAgentEvent({ type: "proposal", id: card.id, status: "stale" });
  try {
    await api.setProposalStatus(projectName, card.id, "stale");
  } catch {
    // best-effort — the card is already greyed locally and the doc is resynced
  }
  toast("Claude's edit no longer applies — ask it to regenerate.");
}

export async function rejectProposal(
  projectName: string,
  card: ProposalCard,
  reason: string,
): Promise<void> {
  useChatStore.getState().ingestAgentEvent({ type: "proposal", id: card.id, status: "rejected" });
  try {
    await api.setProposalStatus(projectName, card.id, "rejected", reason);
  } catch (e) {
    toast(`Reject failed: ${e}`);
  }
}

// The "just do it" auto-accept: fires for ONLY the proposal that arrived in the CURRENT armed
// turn (armedTurnId), once, then disarms. The turn binding is what stops a stale arm from a proposal-less
// turn from grabbing a later Pick-triggered proposal or an older lingering pending card. Spends the arm
// BEFORE the async accept so a second proposal in the same turn is not swept up. Returns whether it fired.
export async function maybeAutoAccept(projectName: string, card: ProposalCard): Promise<boolean> {
  const st = useChatStore.getState();
  if (!st.autoApproveArmed || st.armedTurnId === null) return false;
  if (card.status !== "pending" || card.turnId !== st.armedTurnId) return false;
  useChatStore.setState({ autoApproveArmed: false, armedTurnId: null });
  await acceptProposal(projectName, card, true);
  return true;
}
