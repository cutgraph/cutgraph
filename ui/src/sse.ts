import { useEffect } from "react";
import { api } from "./api";
import { useProjectStore, applyRemote } from "./store/useProjectStore";
import { isSelfPending } from "./selfPending";
import { ingestAgentEvent, reconcileChat, setConnected } from "./store/useChatStore";
import { reconcileTranscript } from "./transcript/useTranscript";

// Server pushes revision-only {type:"project", revision} on every accepted write.
// The client re-GETs the full project — the single code
// path for "adopt newer state" — guarded so a stale/echoed revision is ignored.
export function connectProjectSSE(p: string): () => void {
  let source: EventSource | null = null;
  let closed = false;
  let backoff = 500;
  let opened = false; // becomes true after the first successful open; a later open is a RECONNECT
  const MAX_BACKOFF = 10_000;
  // Live SSE job events remain compatible with the historical wire shape and may not carry the
  // durable creation timestamp. Enrich each job once from the authoritative endpoint so the Jobs
  // surface can sort and label a newly-created job without waiting for reload/reconcile.
  const enrichedJobIds = new Set<string>();

  // The one "adopt newer state" path. Re-GET, then re-check the fetched revision against
  // the store: the SSE broadcast can race the write response (and a reconnect catch-up can
  // duplicate an already-adopted revision), so a stale/echoed GET must not applyRemote.
  const adoptNewer = () => {
    api
      .getProject(p)
      .then(({ project, revision }) => {
        if (revision > useProjectStore.getState().revision) applyRemote(project, revision);
      })
      .catch(() => {});
  };

  const open = () => {
    if (closed) return;
    source = new EventSource(`/api/events?project=${encodeURIComponent(p)}`);

    // On every open — including reconnects — catch up on revisions committed while the
    // stream was down (no server-side event ids exist). The initial connect is a natural
    // no-op once App's GET has adopted the current revision.
    source.onopen = () => {
      backoff = 500;
      setConnected(true);
      adoptNewer();
      // The event stream has no replay.  The transcript lifecycle endpoint is
      // durable, so every initial open/reconnect reconciles it once.
      reconcileTranscript(p);
      // on RECONNECT (a gap where agent chat events may have been missed — SSE has no ids and does
      // not replay), reconcile chat + proposals from the durable stores — the SAME code
      // path as the initial Ask bar mount load. The initial open skips it (the mount effect owns it).
      if (opened) void reconcileChat(p);
      opened = true;
    };

    // One EventSource carries both the project revision contract AND agent chat events:
    // the `project` branch is byte-identical to before (adopt-newer + self-echo guard, never widened);
    // every agent event routes to the standalone chat store and NEVER touches revision/applyRemote.
    source.onmessage = (ev) => {
      let msg: { type?: string; revision?: number };
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      switch (msg.type) {
        case "project":
          if (typeof msg.revision !== "number") return;
          if (isSelfPending(msg.revision)) return;
          if (msg.revision <= useProjectStore.getState().revision) return;
          adoptNewer();
          return;
        case "chat_text":
        case "tool_use":
        case "proposal":
        case "options":
        case "job":
          if ((msg as { kind?: unknown }).kind === "transcribe") reconcileTranscript(p);
          ingestAgentEvent(msg as { type: string });
          if (msg.type === "job" && typeof (msg as { id?: unknown }).id === "string") {
            const id = (msg as { id: string }).id;
            if (!enrichedJobIds.has(id)) {
              enrichedJobIds.add(id);
              void api.getComputeJob(id).then((job) => {
                if (!closed) ingestAgentEvent({ type: "job", ...job });
              }).catch(() => {
                // The stream event remains usable; the next durable reconcile can retry enrichment.
              });
            }
          }
          return;
        case "agent_error":
        case "turn_done":
          ingestAgentEvent(msg as { type: string });
          return;
        default:
          return;
      }
    };

    source.onerror = () => {
      source?.close();
      source = null;
      setConnected(false);
      if (closed) return;
      const wait = backoff;
      backoff = Math.min(backoff * 2, MAX_BACKOFF);
      setTimeout(open, wait);
    };
  };

  open();
  return () => {
    closed = true;
    source?.close();
    source = null;
  };
}

// The picker landing (no ?project=) has no project, so the stream must not open: a null/empty
// project would hit /api/events?project= and the server (which requires the arg) reconnect-loops.
export function useProjectSSE(p: string | null): void {
  useEffect(() => {
    if (!p) return;
    return connectProjectSSE(p);
  }, [p]);
}
