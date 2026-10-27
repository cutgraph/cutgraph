import { plural } from "../common";
import { AddonSettingsLink } from "../Addons";
import { rowStatus, type FlowState, type Row } from "./flow";

const ROWS: { row: Row; label: (s: FlowState) => string }[] = [
  { row: "upload", label: (s) => `Uploading ${s.fileName}` },
  { row: "transcribe", label: () => "Transcribing every word with WhisperX" },
  { row: "scenes", label: (s) => (s.sceneCount != null ? `Laid out your story · ${plural(s.sceneCount, "scene")}` : "Finding the topics") },
];

const ROW_ERROR_HINT: Record<Row, string> = {
  upload: "Couldn't upload the footage.",
  transcribe: "Transcription failed.",
  scenes: "Couldn't lay out the story.",
};

// Pure/presentational: everything it shows comes from `state`, so it renders identically whether
// driven live by the flow store or by a fixture in a test.
export default function ProcessingCard({ state, onRetry }: { state: FlowState; onRetry: () => void }) {
  return (
    <div className="nv-process" aria-live="polite" aria-busy={state.phase !== "ready" && state.phase !== "error"}>
      <p className="eyebrow">From footage</p>
      <h2>{state.phase === "ready" ? "Your story is ready" : "Preparing your footage"}</h2>
      <ol className="nv-steps">
        {ROWS.map(({ row, label }) => {
          const status = rowStatus(state, row);
          return (
            <li key={row} className={status} data-row={row} data-status={status}>
              <i aria-hidden="true" />
              <span>
                {label(state)}
                {status === "active" && state.message && <small>{state.message}</small>}
                {status === "error" && (
                  <span className="nv-error">
                    <small>{state.errorMessage || ROW_ERROR_HINT[row]}</small>
                    {row === "transcribe" && <AddonSettingsLink />}
                    <button type="button" className="ghost" onClick={onRetry}>
                      Retry
                    </button>
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ol>
      {state.phase === "uploading" && (
        <div className="nv-progress" role="progressbar" aria-valuenow={Math.round(state.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
          <i style={{ width: `${Math.round(state.progress * 100)}%` }} />
        </div>
      )}
      <p className="nv-fineprint">Uploads, transcribes and arranges scenes on this computer.</p>
    </div>
  );
}
