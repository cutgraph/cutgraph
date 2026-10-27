// "New character": a profile named here, drawn by a registered rig in one of its palettes, with its
// approved art uploaded into the library of the workspace it lives in. A brand character appears in
// every workspace; one in a client workspace only in that workspace's videos. Every rig is listed; a
// client rig lives only in a client workspace, so under Brand it is marked unavailable and
// picking it moves the character to a client workspace (an existing one, or a new one named inline).
// Its character starts as a draft.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { CHARACTERS, type Character, type MotionEnv } from "motion-core";
import { BUILTIN_WORKSPACES, v3api, type CharacterProfile } from "../api";
import { errorText } from "../common";
import { toast } from "../Toast";
import { nameOf } from "../story/shot/names";
import { CHARACTER_REST_T, paintCharacterTile } from "../story/shot/paint";
import { useTileCanvas } from "../story/shot/Picker";
import type { Cast } from "./cast";
import { createCharacter, updateCharacter } from "./castStore";

function RigPreview({ rig, palette, env }: { rig: string; palette: string; env: MotionEnv }) {
  const ref = useTileCanvas(
    (ctx, w, h, t) => paintCharacterTile(ctx, w, h, { character: rig, palette, pose: "wave", expression: "happy", move: "wave", t: t ?? CHARACTER_REST_T }, env),
    `${rig}|${palette}`,
    true,
  );
  return <canvas ref={ref} width={224} height={240} role="img" aria-label={`The ${CHARACTERS[rig]?.name ?? rig} rig, ${palette}`} />;
}

export default function NewCharacter({ cast, env, onClose, onCreated }: {
  cast: Cast;
  env: MotionEnv;
  onClose: () => void;
  /** `where` is "brand" or the project's name. */
  onCreated: (entry: CharacterProfile, where: string) => void;
}) {
  const project = cast.workspace;
  const [projects, setProjects] = useState<{ id: string; name: string }[]>(project ? [project] : []);
  const [name, setName] = useState("");
  const [picked, setRig] = useState<string | null>(null);
  const [chosenPalette, setPalette] = useState<string | null>(null);
  const [home, setHome] = useState(project?.id ?? "brand");
  const [newProject, setNewProject] = useState<string | null>(null);
  const rigs = Object.values(CHARACTERS);
  const livesHere = (r: Character): boolean => home !== "brand" || r.owner !== "client";
  const rigDef = rigs.find((r) => r.id === picked && livesHere(r)) ?? rigs.find(livesHere) ?? rigs[0];
  const rig = rigDef.id;
  const clientRigs = rigs.filter((r) => r.owner === "client");
  const palette = chosenPalette !== null && rigDef.palettes.includes(chosenPalette) ? chosenPalette : rigDef.palettes[0];
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLFormElement>(null);

  useEffect(() => dialogRef.current?.querySelector("input")?.focus(), []);

  useEffect(() => {
    let live = true;
    v3api.workspaces().then(
      (all) => live && setProjects(all.filter((w) => !BUILTIN_WORKSPACES.has(w.id)).map((w) => ({ id: w.id, name: w.name }))),
      () => {},
    );
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  async function create(e: FormEvent): Promise<void> {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const where = home === "brand" ? "brand" : projects.find((p) => p.id === home)?.name ?? home;
    let entry: CharacterProfile | null = null;
    try {
      entry = await createCharacter({ workspace: home, name, rig, palette });
      if (files.length) {
        const { assets } = await v3api.uploadAssets(home, files);
        entry = await updateCharacter(entry.id, { art: [...new Set(assets.map((a) => a.id))] });
      }
      onCreated(entry, where);
    } catch (err) {
      const text = errorText(err).replace(/^\d{3}\s+/, "");
      if (!entry) {
        setError(text);
        setBusy(false);
        return;
      }
      onCreated(entry, where);
      toast(`${entry.name} was created, but its art didn't upload: ${text}`);
    }
  }

  const homeName = projects.find((p) => p.id === home)?.name ?? home;

  // A client's rig under Brand: the character moves to a client workspace (the open one, else the first,
  // else a new one to name).
  function pickRig(r: Character): void {
    setRig(r.id);
    if (livesHere(r)) return;
    const to = projects.find((p) => p.id === project?.id) ?? projects[0];
    if (to) setHome(to.id);
    else setNewProject("");
  }

  async function addProject(): Promise<void> {
    const clean = (newProject ?? "").trim();
    if (!clean) return;
    setError(null);
    try {
      const made = await v3api.createWorkspace(clean);
      setProjects((ps) => [...ps, { id: made.id, name: made.name }]);
      setHome(made.id);
      setNewProject(null);
    } catch (err) {
      setError(errorText(err).replace(/^\d{3}\s+/, ""));
    }
  }

  return (
    <div className="cast-layer" data-slot="new-character">
      <form className="char-new" role="dialog" aria-modal="true" aria-label="New character" ref={dialogRef} onSubmit={(e) => void create(e)}>
        <header className="composer-top">
          <div>
            <p className="eyebrow">Cast · character</p>
            <h2>New character</h2>
          </div>
          <span className="grow" />
          <button type="button" className="icon" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <div className="char-new-grid">
          <div className="char-new-stage">
            <RigPreview rig={rig} palette={palette} env={env} />
          </div>
          <div className="char-new-form">
            <label className="field">
              <span>Name</span>
              <input value={name} maxLength={40} placeholder="e.g. Notes Reader" aria-label="Name" onChange={(e) => setName(e.target.value)} />
            </label>
            <div className="field">
              <span>Rig</span>
              <div className="seg" role="group" aria-label="Rig">
                {rigs.map((r) => (
                  <button key={r.id} type="button" className={r.id === rig ? "on" : livesHere(r) ? undefined : "off"} aria-pressed={r.id === rig}
                    aria-disabled={!livesHere(r)} title={livesHere(r) ? undefined : `The ${r.name} is a client character; pick it to choose a client workspace`}
                    onClick={() => pickRig(r)}>
                    {r.name}
                  </button>
                ))}
              </div>
              <p className="note">
                {rigDef.owner === "client"
                  ? `Uses the selected rig. The character starts as a draft until you approve its look in the Cast.`
                  : home === "brand" && clientRigs.length
                    ? `${clientRigs.map((r) => `The ${r.name} is a client character; it lives in a client workspace.`).join(" ")} Brand characters use the ${rigDef.name} rig.`
                    : "The rig draws the character: its expressions, poses and moves."}
              </p>
            </div>
            {rigDef.palettes.length > 1 && (
              <div className="field">
                <span>Palette</span>
                <div className="seg" role="group" aria-label="Palette">
                  {rigDef.palettes.map((p) => (
                    <button key={p} type="button" className={p === palette ? "on" : undefined} aria-pressed={p === palette} onClick={() => setPalette(p)}>
                      {nameOf({}, p)}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="field">
              <span>Where it lives</span>
              <div className="seg homes" role="group" aria-label="Where it lives">
                {[{ id: "brand", name: "Brand" }, ...projects].map((h) => (
                  <button key={h.id} type="button" className={h.id === home ? "on" : undefined} aria-pressed={h.id === home} onClick={() => {
                    setHome(h.id);
                    setNewProject(null);
                  }}>
                    {h.name}
                  </button>
                ))}
                <button type="button" className={newProject !== null ? "on" : undefined} aria-pressed={newProject !== null} onClick={() => setNewProject("")}>
                  + New client workspace…
                </button>
              </div>
              {newProject !== null && (
                <div className="new-home">
                  <input value={newProject} maxLength={120} placeholder="e.g. Weeknight Pasta" aria-label="New client workspace"
                    onChange={(e) => setNewProject(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        void addProject();
                      }
                    }} />
                  <button type="button" className="ghost" disabled={!newProject.trim()} onClick={() => void addProject()}>Add workspace</button>
                  <button type="button" className="ghost" onClick={() => setNewProject(null)}>Cancel</button>
                </div>
              )}
              <p className="note">
                {home === "brand"
                  ? "A brand character appears in every workspace's videos."
                  : `Only videos in ${homeName} can cast it. It never appears in another workspace.`}
              </p>
            </div>
            <div className="field">
              <span>Approved art</span>
              <input type="file" accept="image/*" multiple aria-label="Approved art" onChange={(e) => setFiles([...(e.target.files ?? [])])} />
              <p className="note">
                {files.length
                  ? `${files.map((f) => f.name).join(", ")} goes into the ${home === "brand" ? "brand" : homeName} library.`
                  : "The model sheet it is drawn from. Optional now; add it later from the Cast."}
              </p>
            </div>
            {error && <p className="form-error" role="alert">{error}</p>}
            <div className="composer-actions">
              <span className="grow" />
              <button type="button" className="ghost" onClick={onClose}>Cancel</button>
              <button type="submit" className="primary" disabled={busy || !name.trim()}>{busy ? "Creating…" : "Create"}</button>
            </div>
          </div>
        </div>
      </form>
    </div>
  );
}
