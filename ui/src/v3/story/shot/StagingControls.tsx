// Short-film staging in the Shot view: how an animation's frame is composed (the character stage,
// a phone screen, a title card or a sign-off) and its camera move, plus each layout's own
// fields: the phones' library images (1–3 phones; a lone phone can show the generated app screen), the
// title card's badges and the sign-off's pixel letters. On a stage with a co-star or an ensemble its
// labels: the headline over the co-star, and badges under the members. On an independent diagram the
// board's box labels. The headline and eyebrow rows below serve every layout. Every pick is one undo step.
import { useRef, useState } from "react";
import {
  BADGE_TONES, BADGES_MAX, BOARD_LABEL_MAX, BOARD_LABELS_MAX, CAMERAS, LAYOUTS, MARK_MAX, SCREEN_MORE_MAX,
  type BadgeTone, type Camera, type StageLayout,
} from "motion-core";
import type { Badge, PhoneScreen, Shot } from "../../../types/project";
import { v3api } from "../../api";
import { useDraft } from "../../common";
import { importTarget, refreshLibraries, useLibraryAssets } from "./screenLibrary";
import type { ShotPatch } from "./specs";
import "./staging.css";

export const LAYOUT_NAMES: Record<StageLayout, string> = { stage: "Stage", screen: "Phone screen", title: "Title card", signoff: "Sign-off" };
export const CAMERA_NAMES: Record<Camera | "none", string> = { none: "Still", "push-in": "Push in", "pull-out": "Pull out", pan: "Pan" };
const TONE_NAMES: Record<BadgeTone, string> = { ink: "Ink", paper: "Paper", accent: "Accent" };

const LAYOUT_HINT: Record<StageLayout, string> = {
  stage: "Your character on its backdrop, with the diagram and the headline.",
  screen: "Fits 1–3 phone frames, each with a library image, beside or below a headline. A lone phone can show a generated app screen.",
  title: "A title card: the badges drop in one after another, then the headline rises in beneath them. No characters or bubbles show.",
  signoff: "A resting performer beside optional pixel text, with an eyebrow and headline.",
};
const CAMERA_HINT: Record<Camera | "none", string> = {
  none: "The camera holds still.",
  "push-in": "A bounded centre-framed zoom over the scene's last 0.8 s.",
  "pull-out": "Starts with a mild independent crop and returns to the full frame over the scene's first 0.8 s.",
  pan: "Pans over the scene's first 2.4 s, carrying the stage in from the left with your character standing in it; a co-star walking in arrives as it settles. The stage only.",
};

const MARK_RE = /^[A-Za-z0-9&]{1,4}$/;
const BADGE_TEXT_MAX = 24;

type PickShot = (patch: ShotPatch, label: string) => void;

/** A text field that keeps a draft while typing and commits on blur or Enter (Escape restores the stored value). */
function DraftInput({ stored, label, placeholder, maxLength, commit }: {
  stored: string;
  label: string;
  placeholder?: string;
  maxLength: number;
  commit: (value: string) => void;
}) {
  const [draft, setDraft] = useDraft(stored);
  const cancel = useRef(false);
  return (
    <input
      value={draft}
      aria-label={label}
      placeholder={placeholder}
      maxLength={maxLength}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (cancel.current) cancel.current = false;
        else if (draft !== stored) commit(draft);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        else if (e.key === "Escape") {
          e.stopPropagation();
          cancel.current = true;
          setDraft(stored);
          e.currentTarget.blur();
        }
      }}
    />
  );
}

const PHONE_NAMES = ["Phone 1 (front)", "Phone 2", "Phone 3"] as const;

/** A screen's phones as one list of asset ids, phone 1 first; none draws the generated app screen. */
const phoneIds = (screen: PhoneScreen | undefined): string[] => (screen ? [screen.asset, ...(screen.more ?? [])] : []);

const screenOf = (ids: string[]): PhoneScreen | null =>
  ids.length ? { asset: ids[0], ...(ids.length > 1 && { more: ids.slice(1, 1 + SCREEN_MORE_MAX) as NonNullable<PhoneScreen["more"]> }) } : null;

function ScreenImage({ screen, workspaceId, pick }: { screen: PhoneScreen | undefined; workspaceId: string | null | undefined; pick: PickShot }) {
  const images = useLibraryAssets(workspaceId).filter((e) => e.asset.kind === "image");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chosen, choose] = useState(0);
  const file = useRef<HTMLInputElement>(null);
  const ids = phoneIds(screen);
  // A slot past the first empty one is not open yet: phones fill up in order.
  const slot = Math.min(chosen, ids.length, SCREEN_MORE_MAX);
  const find = (id: string) => images.find((e) => e.asset.id === id);
  const missing = images.length > 0 ? ids.flatMap((id, i) => (find(id) ? [] : [i])) : [];

  const put = (next: string[], label: string): void => {
    choose(Math.min(slot, next.length));
    pick({ screen: screenOf(next) }, label);
  };
  const set = (id: string, name: string): void => {
    const next = [...ids];
    next[slot] = id;
    put(next, `${slot ? `Phone ${slot + 1}` : "Phone"} image: ${name}`);
  };
  const clear = (i: number): void => put(ids.filter((_, j) => j !== i), `Clear phone ${i + 1}`);

  async function upload(files: FileList | null): Promise<void> {
    const f = files?.[0];
    if (!f) return;
    setBusy(true);
    setError(null);
    try {
      const { assets } = await v3api.uploadAssets(importTarget(workspaceId), [f]);
      const image = assets.find((a) => a.kind === "image");
      if (!image) throw new Error("That file is not an image.");
      refreshLibraries();
      set(image.id, image.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      if (file.current) file.current.value = "";
    }
  }

  return (
    <div className="rrow">
      <div className="rhead">
        <h4>Phone image</h4>
        <button type="button" className="costar-add" disabled={busy} onClick={() => file.current?.click()}>
          {busy ? "Importing…" : "Import image…"}
        </button>
        <input ref={file} type="file" accept="image/*" hidden aria-label="Import a phone image" onChange={(e) => void upload(e.target.files)} />
      </div>
      <div className="phone-slots" role="group" aria-label="Phones">
        {PHONE_NAMES.map((name, i) => {
          const id = ids[i], hit = id ? find(id) : undefined;
          return (
            <div key={name} className={i === slot ? "phone-slot on" : "phone-slot"}>
              <button type="button" aria-pressed={i === slot} disabled={i > ids.length} title={hit?.asset.name}
                onClick={() => choose(i)}>
                {hit ? <img src={v3api.assetThumbUrl(hit.workspaceId, hit.asset.id)} alt="" loading="lazy" />
                  : <span className={id || i === 0 ? "app-screen" : "empty"} aria-hidden="true">{(id || i === 0) && <><i /><i /><i /><i /></>}</span>}
                <span>{name}</span>
              </button>
              {id && ids.length > 1 && (
                <button type="button" className="icon" aria-label={`Clear phone ${i + 1}`} title={i ? undefined : "Phone 2 moves to the front"}
                  onClick={() => clear(i)}>×</button>
              )}
            </div>
          );
        })}
      </div>
      <div className="strip screen-images" role="group" aria-label="Phone image">
        {slot === 0 && ids.length <= 1 && (
          <button type="button" className={ids.length ? "atile" : "atile on"} aria-pressed={!ids.length} onClick={() => put([], "Phone image: app screen")}>
            <span className="app-screen" aria-hidden="true"><i /><i /><i /><i /></span>
            <span>App screen</span>
          </button>
        )}
        {images.map((e) => (
          <button key={`${e.workspaceId}/${e.asset.id}`} type="button" className={e.asset.id === ids[slot] ? "atile on" : "atile"} aria-pressed={e.asset.id === ids[slot]}
            title={e.asset.name} onClick={() => set(e.asset.id, e.asset.name)}>
            <img src={v3api.assetThumbUrl(e.workspaceId, e.asset.id)} alt="" loading="lazy" />
            <span>{e.asset.name}</span>
          </button>
        ))}
      </div>
      {missing.length > 0 && (
        <p className="mode-hint">
          {ids.length === 1 ? "That image is no longer in the library: the phone shows the app screen."
            : `${missing.map((i) => `Phone ${i + 1}`).join(" and ")}: no longer in the library, so ${missing.length > 1 ? "they show" : "it shows"} the app screen.`}
        </p>
      )}
      {error && <p className="mode-hint staging-error" role="alert">{error}</p>}
    </div>
  );
}

function Badges({ badges, pick, hint = "No badges yet: the headline stands alone." }: { badges: Badge[]; pick: PickShot; hint?: string }) {
  const put = (next: Badge[], label: string): void => pick({ badges: (next.length ? next : null) as Shot["badges"] | null }, label);
  return (
    <div className="rrow badges" data-slot="badges">
      <div className="rhead">
        <h4>Badges</h4>
        <button type="button" className="costar-add" disabled={badges.length >= BADGES_MAX}
          onClick={() => put([...badges, { text: "BADGE", tone: "ink" }], "Add badge")}>
          + Add badge
        </button>
      </div>
      {!badges.length && <p className="mode-hint">{hint}</p>}
      {badges.map((b, i) => (
        <div key={i} className="badge-row">
          <DraftInput stored={b.text} label={`Badge ${i + 1}`} maxLength={BADGE_TEXT_MAX}
            commit={(text) => {
              const t = text.trim();
              put(t ? badges.map((x, j) => (j === i ? { ...x, text: t } : x)) : badges.filter((_, j) => j !== i), t ? "Edit badge" : "Remove badge");
            }} />
          <div className="seg tones" role="group" aria-label={`Badge ${i + 1} tone`}>
            {BADGE_TONES.map((tone) => (
              <button key={tone} type="button" className={tone === b.tone ? "on" : undefined} aria-pressed={tone === b.tone} data-tone={tone}
                onClick={() => put(badges.map((x, j) => (j === i ? { ...x, tone } : x)), `Badge tone: ${TONE_NAMES[tone]}`)}>
                {TONE_NAMES[tone]}
              </button>
            ))}
          </div>
          <button type="button" className="icon" aria-label={`Remove badge ${i + 1}`} onClick={() => put(badges.filter((_, j) => j !== i), "Remove badge")}>×</button>
        </div>
      ))}
    </div>
  );
}

const BOARD_SLOTS = Array.from({ length: BOARD_LABELS_MAX }, (_, i) => `Node ${i + 1}`);

/**
 * Independent diagram labels in node order. Each commit is one undo step; blanks
 * at the end are dropped and a board with none left draws three blank nodes.
 */
export function BoardLabels({ labels, pick }: { labels: readonly string[]; pick: PickShot }) {
  const put = (i: number, text: string): void => {
    const next = BOARD_SLOTS.map((_, j) => (j === i ? text.trim() : labels[j] ?? ""));
    while (next.length && !next[next.length - 1]) next.pop();
    pick({ boardLabels: (next.length ? next : null) as Shot["boardLabels"] | null }, text.trim() ? `Board label: ${text.trim()}` : "Clear board label");
  };
  return (
    <div className="rrow board-labels" data-slot="board-labels">
      <h4>Board labels</h4>
      <div className="board-label-grid">
        {BOARD_SLOTS.map((slot, i) => (
          <DraftInput key={slot} stored={labels[i] ?? ""} label={`Board label ${slot.toLowerCase()}`} placeholder={slot} maxLength={BOARD_LABEL_MAX}
            commit={(text) => put(i, text)} />
        ))}
      </div>
      <p className="mode-hint">One to six entries size the board. Grid: by row; chain: along its path; radial: centre then clockwise from the top; tree: by level; list: top to bottom. Blank entries draw empty boxes. No labels draws three blank nodes.</p>
    </div>
  );
}

/** A stage's labels: its headline over the co-star, and badges under the ensemble's members. */
function StageLabels({ shot, pick }: { shot: Shot; pick: PickShot }) {
  const over = shot.headlineAt === "costar";
  return (
    <>
      {shot.costar && (
        <div className="rrow" data-slot="headline-at">
          <h4>Headline place</h4>
          <div className="seg" role="group" aria-label="Headline place">
            {([[false, "Its own", "Headline in its own place"], [true, "Over the co-star", "Headline over the co-star"]] as const).map(([on, name, label]) => (
              <button key={name} type="button" className={on === over ? "on" : undefined} aria-pressed={on === over}
                onClick={() => pick({ headlineAt: on ? "costar" : null }, label)}>
                {name}
              </button>
            ))}
          </div>
          <p className="mode-hint">{over ? "Big title lettering over the co-star, fading in a quarter of the way through the scene, clear of the diagram." : "The headline in its usual place over the stage."}</p>
        </div>
      )}
      {!!shot.ensemble?.length && (
        <Badges badges={[...(shot.badges ?? [])]} pick={pick}
          hint="Badges drop in one under each ensemble member, from about halfway through the scene." />
      )}
    </>
  );
}

function Mark({ mark, pick }: { mark: string | undefined; pick: PickShot }) {
  const [bad, setBad] = useState(false);
  return (
    <div className="rrow">
      <h4>Letters</h4>
      <DraftInput stored={mark ?? ""} label="Letters" placeholder="Optional text" maxLength={MARK_MAX}
        commit={(value) => {
          const v = value.trim();
          setBad(!!v && !MARK_RE.test(v));
          if (!v) pick({ mark: null }, "Clear sign-off text");
          else if (MARK_RE.test(v)) pick({ mark: v.toUpperCase() }, `Letters: ${v.toUpperCase()}`);
        }} />
      <p className="mode-hint">{bad ? "Up to 4 letters, digits or &." : "Empty leaves the mark unbranded."}</p>
    </div>
  );
}

/** The staging rows for an animation's stored shot. */
export default function StagingControls({ shot, workspaceId, onPick }: {
  shot: Shot | undefined;
  workspaceId: string | null | undefined;
  onPick: PickShot;
}) {
  const layout: StageLayout = shot?.layout ?? "stage";
  const camera: Camera | "none" = shot?.camera ?? "none";
  return (
    <div className="staging" data-slot="staging">
      <div className="rrow">
        <h4>Layout</h4>
        <div className="seg" role="group" aria-label="Layout">
          {LAYOUTS.map((id) => (
            <button key={id} type="button" className={id === layout ? "on" : undefined} aria-pressed={id === layout}
              onClick={() => onPick({ layout: id === "stage" ? null : id }, `Layout: ${LAYOUT_NAMES[id]}`)}>
              {LAYOUT_NAMES[id]}
            </button>
          ))}
        </div>
        <p className="mode-hint">{LAYOUT_HINT[layout]}</p>
      </div>
      {layout === "screen" && <ScreenImage screen={shot?.screen} workspaceId={workspaceId} pick={onPick} />}
      {layout === "title" && <Badges badges={[...(shot?.badges ?? [])]} pick={onPick} />}
      {layout === "signoff" && <Mark mark={shot?.mark} pick={onPick} />}
      {layout === "stage" && shot && (shot.costar || !!shot.ensemble?.length) && <StageLabels shot={shot} pick={onPick} />}
      <div className="rrow">
        <h4>Camera</h4>
        <div className="seg" role="group" aria-label="Camera">
          {(["none", ...CAMERAS] as const).map((id) => (
            <button key={id} type="button" className={id === camera ? "on" : undefined} aria-pressed={id === camera}
              onClick={() => onPick({ camera: id === "none" ? null : id }, `Camera: ${CAMERA_NAMES[id]}`)}>
              {CAMERA_NAMES[id]}
            </button>
          ))}
        </div>
        <p className="mode-hint">{CAMERA_HINT[camera]}</p>
      </div>
    </div>
  );
}
