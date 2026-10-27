// Library → Cast: the characters this workspace can cast,
// then for the selected one its approved model sheet, the live rig, and every expression, pose and
// move as a tile motion-core draws. Tiles rest until hovered. Custom expressions sit after the
// built-ins; the composer makes new ones and "New character" adds a profile to the brand or project.
// A character's rig and palette can change here, and a client rig's draft look is approved here.
// Each card has its own actions (⋯: Rename, Make default, Delete); deleting says how many scenes use
// the character first, and the built-in Lens can't be deleted. A described expression lands as a
// Draft with Approve / Discard, and no scene can wear it until it is approved.
import { useCallback, useEffect, useRef, useState, type ChangeEvent, type CSSProperties, type FormEvent, type ReactNode } from "react";
import { DEFAULT_CHARACTER, CHARACTERS } from "motion-core";
import { domEnv } from "../../preview/SceneLayer";
import { v3api, type CastExpression, type CastScope, type CharacterPatch, type CharacterProfile, type CharacterUsage } from "../api";
import { errorText, plural } from "../common";
import ProjectSwitcher from "../ProjectSwitcher";
import { navigate } from "../router";
import { EXPRESSION_NAMES, MOVE_NAMES, POSE_NAMES, nameOf } from "../story/shot/names";
import { CHARACTER_REST_T, paintCharacterTile } from "../story/shot/paint";
import { useTileCanvas, type TilePaint } from "../story/shot/Picker";
import { toast } from "../Toast";
import { DEFAULT_PROFILE, characterFor, expressionName, homeName, isBuiltInExpression, performerOf, rigOf, rigsFor, type Cast } from "./cast";
import { createExpression, deleteCharacter, deleteExpression, setDefaultCharacter, updateCharacter, updateExpression, useCast } from "./castStore";
import Composer from "./Composer";
import NewCharacter from "./NewCharacter";

const ENV = domEnv();
const RULES = ["Pixel-crisp: whole-pixel scaling", "Expressions, poses and moves compose freely", "Readable on dark backdrops"];

interface Rig {
  pose: string;
  expression: string;
  move: string;
}

type Kind = keyof Rig;

type ComposerState = { editing?: CastExpression } | null;

interface TileItem {
  id: string;
  label: string;
  custom?: boolean;
  draft?: boolean;
}

interface Art {
  src: string;
  alt: string;
}

function artOf(profile: CharacterProfile): Art[] {
  const uploaded = profile.art.map((id, i) => ({ src: v3api.assetFileUrl(profile.workspaceId, id), alt: `${profile.name}, approved art ${i + 1}` }));
  return uploaded;
}

function CastTile({ item, on, look, paint, onPick }: { item: TileItem; on: boolean; look: string; paint: TilePaint; onPick: () => void }) {
  const [hover, setHover] = useState(false);
  const ref = useTileCanvas(paint, look, hover);
  return (
    <button
      type="button"
      className={`atile big${on ? " on" : ""}`}
      aria-pressed={on}
      data-tile={item.id}
      onClick={onPick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <canvas ref={ref} width={280} height={300} aria-hidden="true" />
      <span>{item.label}</span>
      {item.custom && <em>Custom</em>}
    </button>
  );
}

// Preview a draft expression on the rig, then Approve or Discard.
function DraftTile({ item, on, look, paint, onPick, onApprove, onDiscard }: {
  item: TileItem;
  on: boolean;
  look: string;
  paint: TilePaint;
  onPick: () => void;
  onApprove: () => void;
  onDiscard: () => void;
}) {
  const [hover, setHover] = useState(false);
  const ref = useTileCanvas(paint, look, hover);
  return (
    <div className={`atile big drafted${on ? " on" : ""}`} data-tile={item.id} role="group" aria-label={`${item.label}, a draft`}>
      <button type="button" className="draft-pick" aria-pressed={on} aria-label={item.label} onClick={onPick}
        onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        <canvas ref={ref} width={280} height={300} aria-hidden="true" />
        <span>{item.label}</span>
      </button>
      <em>Draft</em>
      <div className="draft-actions">
        <button type="button" className="primary" onClick={onApprove}>Approve</button>
        <button type="button" className="ghost" onClick={onDiscard}>Discard</button>
      </div>
    </div>
  );
}

function NewTile({ onNew, onDescribe, busy }: { onNew: () => void; onDescribe: (text: string) => void; busy: boolean }) {
  const [text, setText] = useState("");
  return (
    <form
      className="atile big new"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) onDescribe(text.trim());
        else onNew();
        setText("");
      }}
    >
      <button type="button" className="plus" aria-label="New expression" onClick={onNew}>＋</button>
      <input value={text} placeholder={busy ? "Drafting…" : "Describe: e.g. proud"} aria-label="Describe a new expression" disabled={busy}
        onChange={(e) => setText(e.target.value)} />
    </form>
  );
}

function Section({ kind, title, items, rig, look, profile, onPick, onApprove, onDiscard, children }: {
  kind: Kind;
  title: string;
  items: TileItem[];
  rig: Rig;
  look: string;
  profile: CharacterProfile;
  onPick: (kind: Kind, id: string) => void;
  onApprove?: (id: string) => void;
  onDiscard?: (id: string) => void;
  children?: ReactNode;
}) {
  const paint = (id: string): TilePaint => (ctx, w, h, t) =>
    paintCharacterTile(ctx, w, h, performerOf(profile, { ...rig, [kind]: id, t: t ?? CHARACTER_REST_T }), ENV);
  return (
    <section className="lib-sec" aria-label={title}>
      <h2>
        {title} <small>{items.length}</small>
      </h2>
      <div className="cast-grid">
        {items.map((item) => item.draft && onApprove && onDiscard
          ? <DraftTile key={item.id} item={item} on={rig[kind] === item.id} look={look} paint={paint(item.id)} onPick={() => onPick(kind, item.id)}
              onApprove={() => onApprove(item.id)} onDiscard={() => onDiscard(item.id)} />
          : <CastTile key={item.id} item={item} on={rig[kind] === item.id} look={look} paint={paint(item.id)} onPick={() => onPick(kind, item.id)} />)}
        {children}
      </div>
    </section>
  );
}

function CharacterCard({ profile, cast, on, menu, onPick, onMenu }: {
  profile: CharacterProfile;
  cast: Cast;
  on: boolean;
  menu: boolean;
  onPick: () => void;
  onMenu: (anchor: HTMLElement) => void;
}) {
  const paint: TilePaint = (ctx, w, h) => paintCharacterTile(ctx, w, h, performerOf(profile, { pose: "rest", move: "idle", t: CHARACTER_REST_T }), ENV);
  const ref = useTileCanvas(paint, `${profile.id}|${profile.rig}|${profile.palette}`, false);
  const details = `${homeName(profile, cast)}${profile.id === cast.default ? " · Default" : ""}${profile.available === false ? " · Unavailable" : !profile.approved ? " · Draft" : ""}`;
  return (
    <div className="char-card-wrap">
      <button type="button" className={`char-card${on ? " on" : ""}`} aria-label={`${profile.name} ${details}`} aria-pressed={on} data-character={profile.id} onClick={onPick}>
        <canvas ref={ref} width={112} height={120} aria-hidden="true" />
        <span className="char-text">
          <b>{profile.name}</b>
          <small>{details}</small>
        </span>
      </button>
      <button type="button" className="char-more" aria-label={`Actions for ${profile.name}`} aria-haspopup="menu" aria-expanded={menu}
        onClick={(e) => onMenu(e.currentTarget)}>
        ⋯
      </button>
    </div>
  );
}

/** How many scenes use a character, fetched while `when` holds: null until it arrives (or if it fails). */
function useUsage(id: string, when: boolean): CharacterUsage | null {
  const [usage, setUsage] = useState<CharacterUsage | null>(null);
  useEffect(() => {
    setUsage(null);
    if (!when) return;
    let live = true;
    v3api.characterUsage(id).then((u) => live && setUsage(u), () => {});
    return () => {
      live = false;
    };
  }, [id, when]);
  return usage;
}

function deleteNote(name: string, usage: CharacterUsage | null): string {
  if (!usage) return `Delete ${name} and its expressions?`;
  if (!usage.scenes) return `No scene uses ${name}. Delete it and its expressions?`;
  return `${plural(usage.scenes, "scene")} use${usage.scenes === 1 ? "s" : ""} ${name}; they'll fall back to the default character and their role look.`;
}

const MENU_W = 272;

/** A card's actions: Rename, Make default, Delete (confirmed, with the scenes it changes). */
function CharacterMenu({ profile, cast, anchor, onClose, onDefault, onDelete }: {
  profile: CharacterProfile;
  cast: Cast;
  anchor: HTMLElement;
  onClose: () => void;
  onDefault: (p: CharacterProfile) => Promise<void>;
  onDelete: (p: CharacterProfile) => Promise<void>;
}) {
  const [mode, setMode] = useState<"menu" | "rename" | "delete">("menu");
  const [name, setName] = useState(profile.name);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const usage = useUsage(profile.id, mode === "delete");
  const isBuiltIn = profile.id === DEFAULT_PROFILE.id;
  const isDefault = profile.id === cast.default;
  const box = anchor.getBoundingClientRect();
  const style: CSSProperties = {
    position: "fixed", top: box.bottom + 6, width: MENU_W, right: "auto",
    left: Math.max(8, Math.min(box.right - MENU_W, window.innerWidth - MENU_W - 8)),
  };

  useEffect(() => {
    const close = (e: Event): void => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node) && e.target !== anchor) onClose();
    };
    const away = (): void => onClose();
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    window.addEventListener("resize", away);
    window.addEventListener("scroll", away, true);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
      window.removeEventListener("resize", away);
      window.removeEventListener("scroll", away, true);
    };
  }, [anchor, onClose]);

  useEffect(() => ref.current?.querySelector<HTMLElement>("input, button:not(:disabled)")?.focus(), [mode]);

  async function rename(e: FormEvent): Promise<void> {
    e.preventDefault();
    const next = name.trim();
    if (!next || next === profile.name) return onClose();
    try {
      await updateCharacter(profile.id, { name: next });
      toast(`Renamed to ${next}.`);
      onClose();
    } catch (err) {
      setError(errorText(err).replace(/^\d{3}\s+/, ""));
    }
  }

  return (
    <div className="menu char-menu" role={mode === "menu" ? "menu" : "dialog"} aria-label={`${profile.name} actions`} style={style} ref={ref}>
      {mode === "menu" && (
        <>
          <button type="button" role="menuitem" onClick={() => setMode("rename")}><b>Rename</b></button>
          <button type="button" role="menuitem" disabled={isDefault} onClick={() => void onDefault(profile).then(onClose)}>
            <b>{isDefault ? "Default character" : "Make default"}</b>
            <small>{cast.workspace ? `For ${cast.workspace.name}'s videos` : "For every video without a workspace default"}</small>
          </button>
          <button type="button" role="menuitem" className="danger" disabled={isBuiltIn} onClick={() => setMode("delete")}>
            <b>Delete</b>
            {isBuiltIn && <small>The Lens is built in and can't be deleted.</small>}
          </button>
        </>
      )}
      {mode === "rename" && (
        <form className="char-menu-form" onSubmit={(e) => void rename(e)}>
          <input value={name} maxLength={40} aria-label="Character name" onChange={(e) => setName(e.target.value)} />
          {error && <p className="form-error" role="alert">{error}</p>}
          <div>
            <button type="button" className="ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary" disabled={!name.trim()}>Rename</button>
          </div>
        </form>
      )}
      {mode === "delete" && (
        <div className="char-menu-form">
          <p className="confirm">{deleteNote(profile.name, usage)}</p>
          <div>
            <button type="button" className="ghost" onClick={onClose}>Keep</button>
            <button type="button" className="ghost danger" onClick={() => void onDelete(profile).then(onClose)}>Delete {profile.name}</button>
          </div>
        </div>
      )}
    </div>
  );
}

function ArtCard({ profile }: { profile: CharacterProfile }) {
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const art = artOf(profile);

  async function approve(): Promise<void> {
    setBusy(true);
    try {
      await updateCharacter(profile.id, { approved: true });
      toast(`Approved ${profile.name}'s look. Its shots are no longer drafts.`);
    } catch (err) {
      toast(`Couldn't approve it: ${errorText(err)}`);
    } finally {
      setBusy(false);
    }
  }

  async function upload(e: ChangeEvent<HTMLInputElement>): Promise<void> {
    const files = [...(e.target.files ?? [])];
    e.target.value = "";
    if (!files.length) return;
    setBusy(true);
    try {
      const { assets } = await v3api.uploadAssets(profile.workspaceId, files);
      await updateCharacter(profile.id, { art: [...new Set([...profile.art, ...assets.map((a) => a.id)])] });
      toast(`Added ${files.length === 1 ? "the art" : `${files.length} images`} to ${profile.name}'s model sheet.`);
    } catch (err) {
      toast(`Couldn't add the art: ${errorText(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="hero-card">
      <p className="eyebrow">Model sheet · approved art</p>
      {art.length ? (
        <div className="imgs">
          {art.map((a) => <img key={a.src} src={a.src} alt={a.alt} />)}
        </div>
      ) : (
        <div className="imgs none">
          <p className="muted">No approved art yet. Upload the model sheet this character is drawn from.</p>
        </div>
      )}
      <div className="art-foot">
        <p className="muted">
          {profile.id === DEFAULT_PROFILE.id
            ? "The source of truth for how Lens looks. Nothing below replaces it."
            : `The source of truth for how ${profile.name} looks. It stays in this character's own library.`}
        </p>
        <button type="button" className="ghost" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Uploading…" : "Upload art"}
        </button>
        <input ref={input} type="file" accept="image/*" multiple hidden aria-label={`Upload approved art for ${profile.name}`} onChange={(e) => void upload(e)} />
      </div>
      {!profile.approved && (
        <div className="approve-row">
          <span className="draft">Draft</span>
          <p className="muted">Check the rig against the model sheet. Shots mark {profile.name} as a draft until its look is approved.</p>
          <button type="button" className="primary" disabled={busy} onClick={() => void approve()}>Approve this look</button>
        </div>
      )}
    </div>
  );
}

const items = (ids: readonly string[], names: Record<string, string>): TileItem[] => ids.map((id) => ({ id, label: nameOf(names, id) }));

function Choice({ label, options, value, onPick }: {
  label: string;
  options: { id: string; label: string }[];
  value: string;
  onPick: (id: string) => void;
}) {
  return (
    <div className="choice">
      <span>{label}</span>
      <div className="seg" role="group" aria-label={label}>
        {options.map((o) => (
          <button key={o.id} type="button" className={o.id === value ? "on" : undefined} aria-pressed={o.id === value}
            onClick={() => o.id !== value && onPick(o.id)}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function CastView({ scope = {} }: { scope?: CastScope }) {
  const { cast, status, error } = useCast(scope);
  const [selected, setSelected] = useState<string | null>(null);
  const [rig, setRig] = useState<Rig>({ pose: "point", expression: "happy", move: "idle" });
  const [composer, setComposer] = useState<ComposerState>(null);
  const [drafting, setDrafting] = useState(false);
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [menu, setMenu] = useState<{ id: string; anchor: HTMLElement } | null>(null);
  const closeMenu = useCallback(() => setMenu(null), []);

  const profile = characterFor(cast, selected ?? cast.default);
  const rigDef = rigOf(profile);
  const known = (list: readonly string[], id: string): string => (list.includes(id) ? id : list[0]);
  const expression = isBuiltInExpression(rig.expression, rigDef) || profile.expressions.some((e) => e.id === rig.expression)
    ? rig.expression
    : known(Object.keys(rigDef.expressions), "happy");
  const shown: Rig = { pose: known(rigDef.poses, rig.pose), expression, move: known(rigDef.moves, rig.move) };
  const selectedCustom = profile.expressions.find((e) => e.id === expression);
  const look = `${profile.id}|${shown.pose}|${shown.expression}|${shown.move}|${JSON.stringify(profile.expressions)}`;
  const heroPaint: TilePaint = (ctx, w, h, t) => paintCharacterTile(ctx, w, h, performerOf(profile, { ...shown, t: t ?? 0 }), ENV);
  const heroRef = useTileCanvas(heroPaint, look, true);
  const summary = [nameOf(POSE_NAMES, shown.pose), expressionName(shown.expression, profile), nameOf(MOVE_NAMES, shown.move)].join(" · ");
  const isBuiltIn = profile.id === DEFAULT_PROFILE.id;
  const home = cast.workspace ?? { id: "brand", name: "Brand" };
  const isDefault = profile.id === cast.default;
  const rigChoices = isBuiltIn ? [] : rigsFor(profile.workspaceId);
  const usage = useUsage(profile.id, confirmDelete);

  const pick = (kind: Kind, id: string): void => setRig((r) => ({ ...r, [kind]: id }));
  const close = useCallback(() => setComposer(null), []);
  const select = (id: string): void => {
    setSelected(id);
    setConfirmDelete(false);
  };
  const expressionItems: TileItem[] = [
    ...items(Object.keys(rigDef.expressions), EXPRESSION_NAMES),
    ...profile.expressions.map((e) => ({ id: e.id, label: e.name, custom: true, draft: e.draft })),
  ];

  async function draftExpression(text: string): Promise<void> {
    setDrafting(true);
    try {
      const proposal = await v3api.describeExpression(text, rigDef.id);
      const entry = await createExpression(profile.id, proposal.name, proposal.spec, true);
      setRig((r) => ({ ...r, expression: entry.id }));
      toast(`${proposal.source === "keywords" ? "Drafted" : "Claude drafted"} “${entry.name}” for ${profile.name}. Approve it to use it in any scene.`);
    } catch (err) {
      toast(`Couldn't draft it: ${errorText(err).replace(/^\d{3}\s+/, "")}`);
    } finally {
      setDrafting(false);
    }
  }

  async function approveExpression(id: string): Promise<void> {
    try {
      const entry = await updateExpression(profile.id, id, { approved: true });
      toast(`Approved. “${entry.name}” can now be used in any scene.`);
    } catch (err) {
      toast(`Couldn't approve it: ${errorText(err)}`);
    }
  }

  async function discardExpression(id: string): Promise<void> {
    try {
      await deleteExpression(profile.id, id);
      setRig((r) => (r.expression === id ? { ...r, expression: "happy" } : r));
    } catch (err) {
      toast(`Couldn't discard it: ${errorText(err)}`);
    }
  }

  async function makeDefault(p: CharacterProfile = profile): Promise<void> {
    try {
      await setDefaultCharacter(home.id, p.id);
      toast(cast.workspace ? `${p.name} is now the default in ${home.name}.` : `${p.name} is now the default character.`);
    } catch (err) {
      toast(`Couldn't set the default: ${errorText(err)}`);
    }
  }

  async function restyle(patch: CharacterPatch, done: string): Promise<void> {
    try {
      await updateCharacter(profile.id, patch);
      toast(done);
    } catch (err) {
      toast(`Couldn't change it: ${errorText(err).replace(/^\d{3}\s+/, "")}`);
    }
  }

  async function remove(p: CharacterProfile = profile): Promise<void> {
    try {
      await deleteCharacter(p.id);
      if (p.id === profile.id) setSelected(null);
      setConfirmDelete(false);
      toast(`Deleted ${p.name}. Scenes that cast it now use the default character.`);
    } catch (err) {
      toast(`Couldn't delete it: ${errorText(err)}`);
    }
  }

  return (
    <div className="cast">
      <div className="cast-scope" role="note" aria-label="Characters shown">
        {cast.workspace
          ? <span>Showing: <b>Brand</b> + <b>{cast.workspace.name}</b></span>
          : <span>Showing: <b>Brand only</b> · pick a workspace to see its characters</span>}
        <span aria-hidden="true">·</span>
        <ProjectSwitcher route={{ view: "library", ws: scope.workspace ?? cast.workspace?.id }} label={cast.workspace ? "Change" : "Pick a workspace"} />
      </div>
      <div className="char-switch" role="group" aria-label="Characters">
        {cast.characters.map((c) => (
          <CharacterCard key={c.id} profile={c} cast={cast} on={c.id === profile.id} menu={menu?.id === c.id} onPick={() => select(c.id)}
            onMenu={(anchor) => setMenu((m) => (m?.id === c.id ? null : { id: c.id, anchor }))} />
        ))}
        <button type="button" className="char-card new" onClick={() => setCreating(true)}>
          <span aria-hidden="true">＋</span>
          <b>New character</b>
        </button>
      </div>
      {menu && cast.characters.some((c) => c.id === menu.id) && (
        <CharacterMenu key={menu.id} profile={characterFor(cast, menu.id)} cast={cast} anchor={menu.anchor} onClose={closeMenu}
          onDefault={makeDefault} onDelete={remove} />
      )}
      {status === "error" && <p className="load-note" role="alert">The cast didn't load: {error}</p>}
      <div className="cast-hero">
        <ArtCard key={profile.id} profile={profile} />
        <div className="hero-card rig">
          <p className="eyebrow">The rig · drawn live by motion-core</p>
          <canvas ref={heroRef} width={560} height={420} role="img" aria-label={`${profile.name}: ${summary}`} />
          <p className="muted">
            {isBuiltIn
              ? "The default character is drawn from its expression, pose and move."
              : rigDef.id === DEFAULT_CHARACTER
                ? `Drawn with the Lens rig as a placeholder. Choose expressions, poses and moves for ${profile.name}, then approve its look here.`
                : `Drawn with the ${rigDef.name} rig. Its parts (${Object.keys(rigDef.parts).join(", ")}, arms) support the expressions, poses and moves shown below.`}
          </p>
          {profile.workspaceId === "brand" && !isBuiltIn && (
            <p className="muted rig-note">
              {Object.values(CHARACTERS).filter((r) => r.owner === "client").map((r) => `The ${r.name} is a client character; it lives in a client workspace.`).join(" ")}
              {" "}To draw {profile.name} with one, make it there with New character.
            </p>
          )}
          {(rigChoices.length > 1 || rigDef.palettes.length > 1) && (
            <div className="rig-pick">
              {rigChoices.length > 1 && (
                <Choice label="Rig" options={rigChoices.map((r) => ({ id: r.id, label: r.name }))} value={rigDef.id}
                  onPick={(id) => void restyle({ rig: id }, `${profile.name} is now drawn by the ${rigChoices.find((r) => r.id === id)?.name} rig.`)} />
              )}
              {rigDef.palettes.length > 1 && (
                <Choice label="Palette" options={rigDef.palettes.map((p) => ({ id: p, label: nameOf({}, p) }))} value={profile.palette}
                  onPick={(p) => void restyle({ palette: p }, `${profile.name} is now ${p}.`)} />
              )}
            </div>
          )}
          <p className="rig-now">
            <span>{summary}</span>
            {selectedCustom && (
              <button type="button" className="ghost" onClick={() => setComposer({ editing: selectedCustom })}>
                Edit “{selectedCustom.name}”
              </button>
            )}
          </p>
          <p className="char-actions">
            {isDefault
              ? <span className="badge">{cast.workspace ? `Default in ${home.name}` : "Default character"}</span>
              : <button type="button" className="ghost" onClick={() => void makeDefault(profile)}>{cast.workspace ? `Make default in ${home.name}` : "Make default"}</button>}
            {!isBuiltIn && !confirmDelete && (
              <button type="button" className="ghost danger" onClick={() => setConfirmDelete(true)}>Delete character</button>
            )}
            {!isBuiltIn && confirmDelete && (
              <>
                <span className="confirm">{deleteNote(profile.name, usage)}</span>
                <button type="button" className="ghost danger" onClick={() => void remove(profile)}>Delete it</button>
                <button type="button" className="ghost" onClick={() => setConfirmDelete(false)}>Keep</button>
              </>
            )}
          </p>
        </div>
      </div>
      {isBuiltIn && (
        <div className="rules">
          {RULES.map((r) => (
            <span key={r}>{r}</span>
          ))}
        </div>
      )}
      <Section kind="expression" title="Expressions" items={expressionItems} rig={shown} look={look} profile={profile} onPick={pick}
        onApprove={(id) => void approveExpression(id)} onDiscard={(id) => void discardExpression(id)}>
        <NewTile onNew={() => setComposer({})} onDescribe={(text) => void draftExpression(text)} busy={drafting} />
      </Section>
      <Section kind="pose" title="Poses" items={items(rigDef.poses, POSE_NAMES)} rig={shown} look={look} profile={profile} onPick={pick} />
      <Section kind="move" title="Moves" items={items(rigDef.moves, MOVE_NAMES)} rig={shown} look={look} profile={profile} onPick={pick} />
      <p className="usage">
        In a scene's script source it reads{" "}
        <code>{`<Character ${isBuiltIn ? "" : `character="${profile.id}" `}pose="${shown.pose}" expression="${shown.expression}" move="${shown.move}" />`}</code>
      </p>
      {composer && (
        <Composer
          key={profile.id}
          character={profile}
          editing={composer.editing}
          pose={shown.pose}
          move={shown.move}
          env={ENV}
          onClose={close}
          onSaved={(entry) => {
            setComposer(null);
            setRig((r) => ({ ...r, expression: entry.id }));
            toast(composer.editing ? `Saved “${entry.name}”.` : `Added “${entry.name}” to ${profile.name}. Pick it in any scene's Shot view.`);
          }}
          onDeleted={(id) => {
            setComposer(null);
            setRig((r) => (r.expression === id ? { ...r, expression: "happy" } : r));
            toast("Deleted. Scenes that used it are back to their role's look.");
          }}
        />
      )}
      {creating && (
        <NewCharacter
          cast={cast}
          env={ENV}
          onClose={() => setCreating(false)}
          onCreated={(entry, where) => {
            setCreating(false);
            setSelected(entry.id);
            if (entry.workspaceId !== "brand" && entry.workspaceId !== cast.workspace?.id) navigate({ view: "library", ws: entry.workspaceId });
            toast(where === "brand"
              ? `Added ${entry.name} to the brand cast. Every video can cast it.`
              : `Added ${entry.name} to ${where}. Only its videos can cast it.`);
          }}
        />
      )}
    </div>
  );
}
