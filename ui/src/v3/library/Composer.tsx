// The expression composer: pick one option for each part of the character's rig (the default rig has eyes
// and mouth; another rig has its own), or describe the look and take the suggestion, watch it
// live, name it and save it to that character's profile.
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { MotionEnv, PartSpec } from "motion-core";
import { v3api, type CastExpression, type CastProposal, type CharacterProfile } from "../api";
import { errorText } from "../common";
import { paintCharacterTile } from "../story/shot/paint";
import { Strip, useTileCanvas, type TilePaint } from "../story/shot/Picker";
import { firstSpec, optionName, partTitle, performerOf, rigOf } from "./cast";
import { createExpression, deleteExpression, updateExpression } from "./castStore";
import { paintHeadTile } from "./paint";
import { AssistantRequired } from "../AssistantRequired";

const PART_REST_T = 0.6;

function proposalNote(p: CastProposal): string {
  if (p.source !== "keywords") return `Suggested by ${p.source}. Adjust any part below.`;
  if (!p.matched.length) return "No keywords I know in that, so it starts neutral. Pick the parts below.";
  return `Suggested from the keywords ${p.matched.map((w) => `“${w}”`).join(", ")}. Adjust any part below.`;
}

export interface ComposerProps {
  character: CharacterProfile;
  /** The expression being edited; a new one when absent. */
  editing?: CastExpression;
  pose: string;
  move: string;
  env: MotionEnv;
  onClose: () => void;
  onSaved: (entry: CastExpression) => void;
  onDeleted: (id: string) => void;
}

export default function Composer({ character, editing, pose, move, env, onClose, onSaved, onDeleted }: ComposerProps) {
  const rig = rigOf(character);
  const [spec, setSpec] = useState<PartSpec>(editing?.spec ?? firstSpec(rig));
  const [name, setName] = useState(editing?.name ?? "");
  const [text, setText] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const suggested = useRef("");
  const dialogRef = useRef<HTMLDivElement>(null);

  const performer = (state: { pose: string; move: string; t: number }, custom: PartSpec) => ({ ...performerOf(character, state), custom });
  const stagePaint: TilePaint = (ctx, w, h, t) => paintCharacterTile(ctx, w, h, performer({ pose, move, t: t ?? 0 }, spec), env);
  const stageRef = useTileCanvas(stagePaint, `${character.id}|${pose}|${move}`, true);

  useEffect(() => dialogRef.current?.focus(), []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  async function suggest(description: string): Promise<void> {
    if (!description.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const proposal = await v3api.describeExpression(description, rig.id);
      setSpec(proposal.spec);
      setNote(proposalNote(proposal));
      setName((current) => (!current.trim() || current === suggested.current ? proposal.name : current));
      suggested.current = proposal.name;
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function save(e: FormEvent): Promise<void> {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSaved(editing ? await updateExpression(character.id, editing.id, { name, spec }) : await createExpression(character.id, name, spec));
    } catch (err) {
      setError(errorText(err).replace(/^\d{3}\s+/, ""));
      setBusy(false);
    }
  }

  async function remove(): Promise<void> {
    if (!editing) return;
    setBusy(true);
    try {
      await deleteExpression(character.id, editing.id);
      onDeleted(editing.id);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  const look = Object.values(spec).join("|");
  const part = (key: string) => (id: string): TilePaint => (ctx, w, h, t) =>
    paintHeadTile(ctx, w, h, performer({ pose: "rest", move: "idle", t: t ?? PART_REST_T }, { ...spec, [key]: id }), env);
  const title = editing ? `Edit “${editing.name}”` : "New expression";

  return (
    <div className="cast-layer" data-slot="composer">
      <div className="composer" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={dialogRef}>
        <header className="composer-top">
          <div>
            <p className="eyebrow">Cast · {character.name} · expression</p>
            <h2>{title}</h2>
          </div>
          <span className="grow" />
          <button type="button" className="icon" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <div className="composer-grid">
          <div className="composer-stage">
            <canvas ref={stageRef} width={480} height={520} role="img" aria-label="Expression preview" />
          </div>
          <div className="composer-form">
            <AssistantRequired offlineFallback onSettings={onClose}>
            <form onSubmit={(e) => { e.preventDefault(); void suggest(text); }}>
              <div className="field">
                <span>Describe it</span>
                <div className="describe-row">
                  <input value={text} placeholder="e.g. smug, one eyebrow up" aria-label="Describe the expression"
                    onChange={(e) => setText(e.target.value)} />
                  <button type="submit" className="ghost" disabled={busy || !text.trim()}>Suggest</button>
                </div>
              </div>
              {note && <p className="describe-note" role="status">{note}</p>}
            </form>
            </AssistantRequired>
            {Object.entries(rig.parts).map(([key, options]) => (
              <Strip key={key} title={partTitle(rig, key)} options={options.map((id) => ({ id, label: optionName(rig, key, id) }))}
                selected={spec[key]} look={look} paint={part(key)} onPick={(id) => setSpec({ ...spec, [key]: id })} />
            ))}
            <form onSubmit={(e) => void save(e)}>
              <label className="field">
                <span>Name</span>
                <input value={name} maxLength={40} placeholder="e.g. Smug" aria-label="Name" onChange={(e) => setName(e.target.value)} />
              </label>
              {error && <p className="form-error" role="alert">{error}</p>}
              <div className="composer-actions">
                {editing && !confirming && (
                  <button type="button" className="ghost danger" disabled={busy} onClick={() => setConfirming(true)}>Delete</button>
                )}
                {editing && confirming && (
                  <>
                    <p className="confirm">Delete “{editing.name}”? Scenes that use it go back to their role's look.</p>
                    <button type="button" className="ghost danger" disabled={busy} onClick={() => void remove()}>Delete it</button>
                    <button type="button" className="ghost" onClick={() => setConfirming(false)}>Keep</button>
                  </>
                )}
                <span className="grow" />
                <button type="button" className="ghost" onClick={onClose}>Cancel</button>
                <button type="submit" className="primary" disabled={busy || !name.trim()}>Save</button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
