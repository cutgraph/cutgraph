// The Story view's narration controls for a script-origin video: "Record narration" in
// the top bar, which opens the record/import sheet. ScriptKind is the top bar's kind label for every
// video: a script-origin one names its timing source, a footage one just says so.
import { useEffect, useRef, useState } from "react";
import { useProjectStore } from "../../store/useProjectStore";
import { reconcileTranscript } from "../../transcript/useTranscript";
import { clock } from "../common";
import { speechMedia } from "../story/model";
import { TranscriptionRequired } from "../Addons";
import { BUSY, importTake, resetNarration, resumeNarration, useNarration, type NarrationPhase, type NarrationState } from "./narrate";
import "../newvideo/NewVideoFlow.css";
import "./Narration.css";

export function ScriptKind() {
  const narration = useProjectStore((s) => s.project?.narration);
  const label = narration === "recorded" ? "Animation · narrated" : narration === "estimated" ? "Animation · estimated timing" : "Footage";
  return <span className="kind">{label}</span>;
}

// The words sidecar follows the speech media, but the transcript cache is keyed by video: once a
// change of speech media is SAVED (an import, or undoing one), fetch the words again.
function useTranscriptFollowsMedia(video: string): void {
  const media = useProjectStore((s) => (s.project ? speechMedia(s.project) : null));
  const revision = useProjectStore((s) => s.revision);
  const seen = useRef({ media, revision });
  useEffect(() => {
    const last = seen.current;
    if (media === last.media) seen.current = { media, revision };
    else if (revision !== last.revision) {
      seen.current = { media, revision };
      reconcileTranscript(video);
    }
  }, [video, media, revision]);
}

const MIC = (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
  </svg>
);

export function NarrationButton({ video }: { video: string }) {
  const narration = useProjectStore((s) => s.project?.narration);
  const phase = useNarration((s) => (s.video === video ? s.phase : "idle"));
  const [open, setOpen] = useState(false);
  useTranscriptFollowsMedia(video);

  useEffect(() => {
    if (narration) void resumeNarration(video);
  }, [video, narration]);

  if (!narration) return null;
  const busy = BUSY.has(phase);
  const label = busy ? "Adding narration…" : narration === "recorded" ? "Replace narration" : "Record narration";
  return (
    <>
      <button
        type="button"
        className={`ghost narrate${narration === "estimated" && !busy ? " todo" : ""}`}
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        {MIC}
        <span className="narrate-label">{label}</span>
      </button>
      {open && <NarrationSheet video={video} recorded={narration === "recorded"} onClose={() => setOpen(false)} />}
    </>
  );
}

type Row = "uploading" | "transcribing" | "aligning";
const ROWS: Row[] = ["uploading", "transcribing", "aligning"];

function rowStatus(state: NarrationState, row: Row): "pending" | "active" | "done" | "error" {
  const order: NarrationPhase[] = ["uploading", "transcribing", "aligning", "done"];
  const at = order.indexOf(state.phase === "error" ? state.failedAt ?? "uploading" : state.phase);
  const own = order.indexOf(row);
  if (state.phase === "error" && at === own) return "error";
  return at > own ? "done" : at === own ? "active" : "pending";
}

function rowLabel(state: NarrationState, row: Row): string {
  if (row === "uploading") return `Uploading ${state.fileName}`;
  if (row === "transcribing") return "Transcribing it with WhisperX";
  if (state.phase === "done" && state.matched != null) {
    return `Lined up your script · ${state.matched} of ${state.total} words heard`;
  }
  return "Lining your script up with the recording";
}

function TakePick({ onPick }: { onPick: (file: File) => void }) {
  const [over, setOver] = useState(false);
  return (
    <label
      className={`nv-drop${over ? " over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const file = e.dataTransfer.files[0];
        if (file) onPick(file);
      }}
    >
      <span className="nv-drop-ic" aria-hidden="true">
        ⤓
      </span>
      <b>Drop your recording here</b>
      <small>or choose a file · WAV, MP3, M4A, or a video</small>
      <input
        className="sr-only"
        type="file"
        aria-label="Choose a narration file"
        accept="audio/*,video/*"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onPick(file);
          e.target.value = "";
        }}
      />
    </label>
  );
}

const canRecord = (): boolean =>
  typeof MediaRecorder !== "undefined" && typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

function extensionFor(type: string): string {
  if (type.includes("ogg")) return "ogg";
  if (type.includes("mp4") || type.includes("aac")) return "m4a";
  return "webm";
}

function Recorder({ onTake }: { onTake: (file: File) => void }) {
  const [recording, setRecording] = useState<{ recorder: MediaRecorder; started: number } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const live = useRef<MediaRecorder | null>(null);

  useEffect(() => () => {
    const recorder = live.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      recorder.stop();
      recorder.stream.getTracks().forEach((t) => t.stop());
    }
  }, []);

  useEffect(() => {
    if (!recording) return;
    const tick = window.setInterval(() => setElapsed((Date.now() - recording.started) / 1000), 250);
    return () => window.clearInterval(tick);
  }, [recording]);

  const start = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const type = recorder.mimeType || "audio/webm";
        onTake(new File(chunks, `narration.${extensionFor(type)}`, { type }));
      };
      recorder.start();
      live.current = recorder;
      setElapsed(0);
      setRecording({ recorder, started: Date.now() });
    } catch (e) {
      setError(e instanceof Error && e.name === "NotAllowedError" ? "Microphone access was blocked." : "Couldn't open the microphone.");
    }
  };

  const stop = () => {
    recording?.recorder.stop();
    live.current = null;
    setRecording(null);
  };

  return (
    <div className="nr-record">
      {recording ? (
        <button type="button" className="ghost nr-rec on" onClick={stop}>
          <i aria-hidden="true" /> Stop · {clock(Math.floor(elapsed))}
        </button>
      ) : (
        <button type="button" className="ghost nr-rec" onClick={() => void start()}>
          <i aria-hidden="true" /> Record with your microphone
        </button>
      )}
      {error && <small className="nr-error">{error}</small>}
    </div>
  );
}

function NarrationSheet({ video, recorded, onClose }: { video: string; recorded: boolean; onClose: () => void }) {
  const state = useNarration();
  const mine = state.video === video ? state : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const pick = (file: File) => void importTake(video, file);
  const choosing = !mine || mine.phase === "idle" || mine.phase === "error";

  return (
    <div className="nr-sheet" role="dialog" aria-label="Narration">
      <header>
        <h2>Narration</h2>
        <button type="button" className="icon" aria-label="Close" onClick={onClose}>
          ×
        </button>
      </header>
      <p className="muted">
        Read your script aloud and bring the recording here. WhisperX transcribes it and lines every script word up with your voice, so scenes,
        shots and cues keep their words.
      </p>
      <p className="nr-now">
        Timing now: <b>{recorded ? "your recorded narration" : "estimated from the script"}</b>
      </p>
      {choosing && (
        <TranscriptionRequired>
          <TakePick onPick={pick} />
          {canRecord() && <Recorder onTake={pick} />}
        </TranscriptionRequired>
      )}
      {mine && mine.phase !== "idle" && (
        <div className="nr-progress" aria-live="polite" data-phase={mine.phase}>
          <ol className="nv-steps">
            {ROWS.map((row) => {
              const status = rowStatus(mine, row);
              return (
                <li key={row} className={status} data-row={row} data-status={status}>
                  <i aria-hidden="true" />
                  <span>
                    {rowLabel(mine, row)}
                    {status === "active" && mine.message && <small>{mine.message}</small>}
                    {status === "error" && <small className="nr-error">{mine.error}</small>}
                  </span>
                </li>
              );
            })}
          </ol>
          {mine.phase === "uploading" && (
            <div className="nv-progress" role="progressbar" aria-valuenow={Math.round(mine.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
              <i style={{ width: `${Math.round(mine.progress * 100)}%` }} />
            </div>
          )}
          {mine.phase === "done" && (
            <button type="button" className="link" onClick={resetNarration}>
              Import another recording
            </button>
          )}
        </div>
      )}
      <p className="nv-fineprint">Real WhisperX transcription. Undo (Ctrl+Z) goes back to the estimated timing.</p>
    </div>
  );
}
