import { useEffect, useId, useMemo, type CSSProperties } from "react";
import { clock, plural } from "../common";
import { navigate } from "../router";
import { roleColor } from "../story/arc/roles";
import { estimate, parseScript, SAMPLE_SCRIPT, SAMPLE_TITLE } from "./script";
import { useNewScript } from "./store";
import "../newvideo/NewVideoFlow.css";
import "./NewScriptSheet.css";
import { AssistantRequired } from "../AssistantRequired";

const PLACEHOLDER = `## Hook: A quick supper
Have you ever cooked pasta with fresh garlic and a little lemon?

## Prepare the ingredients
Boil the water while someone chops garlic beside the kitchen sink.`;

// Write, paste or draft a script here; Create opens its animatic in Story.
export default function NewScriptSheet() {
  const {
    open, from, title, script, busy, error, idea, drafting, draftError,
    setTitle, setScript, setIdea, draftIt, close, create,
  } = useNewScript();
  const scenes = useMemo(() => parseScript(script), [script]);
  const timing = useMemo(() => estimate(scenes), [scenes]);
  const wordCount = scenes.reduce((n, s) => n + s.words.length, 0);
  const scriptId = useId();
  const ideaId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  if (!open) return null;
  const ready = title.trim().length > 0 && scenes.length > 0 && !busy && !drafting;

  return (
    <div className="nv-overlay" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <form
        className="nv-card ns-card"
        role="dialog"
        aria-modal="true"
        aria-label="New video from a script"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) void create();
        }}
      >
        <header>
          <div>
            <p className="eyebrow">{from === "idea" ? "From an idea" : "From a script"}</p>
            <h2>Write the script</h2>
          </div>
          <button type="button" className="ghost" aria-label="Close" onClick={close}>
            ×
          </button>
        </header>
        {from === "idea" && (
          <div className="ns-idea">
            <AssistantRequired onSettings={close}>
            <label htmlFor={ideaId}>What&apos;s the video about?</label>
            <textarea
              id={ideaId}
              value={idea}
              rows={3}
              spellCheck
              disabled={drafting}
              placeholder="A short cooking explainer: make lemon pasta with three ingredients. Starring Lens."
              onChange={(e) => setIdea(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && idea.trim() && !drafting) {
                  e.preventDefault();
                  void draftIt();
                }
              }}
            />
            <div className="ns-idea-row">
              <button type="button" className="primary" disabled={!idea.trim() || drafting} onClick={() => void draftIt()}>
                {drafting ? "Drafting…" : script ? "✦ Redraft it" : "✦ Draft it"}
              </button>
            </div>
            {drafting && (
              <ol className="nv-steps" aria-live="polite">
                <li className="active">
                  <i aria-hidden="true" />
                  <span>Drafting your script with Claude…</span>
                </li>
              </ol>
            )}
            {draftError && !drafting && (
              <p className="ns-draft-error" role="status">
                {draftError}{" "}
                <button type="button" className="link" onClick={() => {
                  close();
                  navigate({ view: "settings", section: "addons" });
                }}>Open Settings</button>
              </p>
            )}
            </AssistantRequired>
          </div>
        )}
        <label className="ns-field">
          Title
          <input type="text" value={title} maxLength={120} placeholder="What's the video called?" onChange={(e) => setTitle(e.target.value)} />
        </label>
        <div className="ns-field">
          <span className="ns-label-row">
            <label htmlFor={scriptId}>Script</label>
            {!script && (
              <button
                type="button"
                className="link"
                onClick={() => {
                  setScript(SAMPLE_SCRIPT);
                  if (!title.trim()) setTitle(SAMPLE_TITLE);
                }}
              >
                Use the sample script
              </button>
            )}
          </span>
          <textarea
            id={scriptId}
            value={script}
            rows={12}
            spellCheck
            placeholder={PLACEHOLDER}
            onChange={(e) => setScript(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && ready) {
                e.preventDefault();
                void create();
              }
            }}
          />
        </div>
        {script === SAMPLE_SCRIPT && <p className="ns-sample">Sample script: edit it, or replace it with your own.</p>}
        <p className="ns-meta" aria-live="polite">
          {scenes.length ? (
            <>
              <b>{plural(scenes.length, "scene")}</b> · {plural(wordCount, "word")} · about {clock(timing.total)}
            </>
          ) : (
            "Start each scene with a ## heading, or separate them with a blank line."
          )}
        </p>
        {scenes.length > 0 && (
          <ol className="ns-scenes" aria-label="Scenes">
            {scenes.map((scene, i) => (
              <li key={i}>
                <span className="role" style={{ "--c": roleColor(scene.role) } as CSSProperties}>
                  {scene.role}
                </span>
                <b>{scene.title}</b>
                <small>{clock(timing.scenes[i])}</small>
              </li>
            ))}
          </ol>
        )}
        {error && (
          <p className="ns-error" role="alert">
            {error}
          </p>
        )}
        <div className="ns-foot">
          <p className="nv-fineprint">
            Timing is estimated from the words until you record the narration. Name a scene&apos;s role in its heading, like <code>## Problem: …</code>
          </p>
          <button type="submit" className="primary" disabled={!ready}>
            {busy ? "Creating…" : "Create video"}
          </button>
        </div>
      </form>
    </div>
  );
}
