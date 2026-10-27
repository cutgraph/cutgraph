import { useEffect, useRef } from "react";
import type { ChatMsg, ToolChip } from "../../../chat/agentEvents";
import { stripContext, toolLabel } from "./ask";
import { renderMarkdown } from "./markdown";

// The latest reply streams above the Ask bar and collapses to one line.
export default function AskSheet({
  exchange,
  chips,
  working,
  error,
  open,
  onToggle,
}: {
  exchange: ChatMsg[];
  chips: ToolChip[];
  working: boolean;
  error: string | null;
  open: boolean;
  onToggle: () => void;
}) {
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [exchange, chips, open]);

  const replies = exchange.filter((m) => m.role === "assistant");
  const status = working ? "Working…" : error ? "Stopped" : replies.length ? "Replied" : "";
  return (
    <section className={`ask-sheet${open ? " open" : ""}`} aria-label="Claude's reply">
      <button type="button" className="ask-sheet-head" aria-expanded={open} onClick={onToggle}>
        <span className="spark" aria-hidden="true">✦</span>
        <b>Claude</b>
        <span className="ask-sheet-status" role="status" aria-live="polite">
          {working && <i className="ask-dot" aria-hidden="true" />}
          {status}
        </span>
        <span className="ask-sheet-toggle">{open ? "Hide" : "Show"}</span>
      </button>
      {open && (
        <div className="ask-sheet-log" ref={logRef} role="log" aria-label="Reply">
          {exchange.map((m, i) => (
            m.role === "user"
              ? <p key={i} className="ask-msg ask-user">{stripContext(m.text)}</p>
              : <div key={i} className="ask-msg ask-assistant">{renderMarkdown(m.text)}</div>
          ))}
          {chips.length > 0 && (
            <p className="ask-tools">
              {chips.map((c, i) => <span key={i}>{toolLabel(c.name)}</span>)}
            </p>
          )}
          {error && <p className="ask-error" role="alert">{error}</p>}
        </div>
      )}
    </section>
  );
}
