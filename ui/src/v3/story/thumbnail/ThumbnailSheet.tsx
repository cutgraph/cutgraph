// The Thumbnail editor, over the Export sheet in the Shot view's layer: the poster large (16:9 or
// the 9:16 cover) with how it reads at YouTube's 168 x 94, and its recipe: the title, the eyebrow, the lead
// in front and its look, the ensemble behind it and the backdrop. The pickers are the Shot view's; every
// pick is one undo step, saved at once.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { BACKDROPS, BADGE_TONES, ENSEMBLE_MAX, POSTER_FORMATS, POSTER_WORDS, drawPosterBackdrop, type BadgeTone, type PosterFormat } from "motion-core";
import { domEnv } from "../../../preview/SceneLayer";
import { useProjectStore } from "../../../store/useProjectStore";
import type { Badge, ThumbnailMember } from "../../../types/project";
import { characterFor, expressionName, performerOf, rigOf } from "../../library/cast";
import { useDraft } from "../../common";
import { useVideoCast } from "../../library/castStore";
import { expressionOptions, wears } from "../shot/ActingSet";
import { BACKDROP_NAMES, POSE_NAMES, nameOf } from "../shot/names";
import { CHARACTER_REST_T, paintCharacterTile } from "../shot/paint";
import { Strip, type TileOption, type TilePaint } from "../shot/Picker";
import { openThumbnail } from "../storyStore";
import PosterPicture from "./PosterPicture";
import { commitThumbnail, membersOf, posterOf, resetThumbnail, thumbnailOf, type ThumbnailPatch } from "./thumbnail";
import "../shot/shot.css";
import "./thumbnail.css";

const ENV = domEnv();
const FORMATS: readonly PosterFormat[] = ["16:9", "9:16"];
const BACKDROP_OPTIONS: TileOption[] = BACKDROPS.map((id) => ({ id, label: BACKDROP_NAMES[id] }));
const options = (ids: readonly string[], names: Record<string, string>): TileOption[] => ids.map((id) => ({ id, label: nameOf(names, id) }));
const TONE_NAMES: Record<BadgeTone, string> = { ink: "Ink", paper: "Paper", accent: "Accent" };
const labelled = (member: ThumbnailMember, badge: Badge | null): ThumbnailMember => {
  const { badge: _old, ...rest } = member;
  return badge ? { ...rest, badge } : rest;
};

const paintBackdrop = (id: string): TilePaint => (ctx, w, h) => {
  ctx.setTransform(w / 1280, 0, 0, h / 720, 0, 0);
  drawPosterBackdrop(ctx, id as (typeof BACKDROPS)[number], 1280, 720);
};

/** A text field that commits on blur or Enter and reverts on Escape. */
function Field({ label, stored, placeholder, maxLength, onCommit, hint }: {
  label: string;
  stored: string;
  placeholder: string;
  maxLength: number;
  onCommit: (value: string) => void;
  hint?: ReactNode;
}) {
  const [draft, setDraft] = useDraft(stored);
  const cancel = useRef(false);
  return (
    <div className="rrow">
      <h4>{label}</h4>
      <input
        value={draft}
        placeholder={placeholder}
        aria-label={label}
        maxLength={maxLength}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          if (cancel.current) cancel.current = false;
          else if (draft.trim() !== stored.trim()) onCommit(draft.trim());
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
      {hint}
    </div>
  );
}

export default function ThumbnailSheet() {
  const project = useProjectStore((s) => s.project);
  const { cast } = useVideoCast();
  const [format, setFormat] = useState<PosterFormat>("16:9");
  const dialogRef = useRef<HTMLDivElement>(null);
  const poster = useMemo(() => (project ? posterOf(project, cast) : null), [project, cast]);
  const thumbnail = useMemo(() => (project ? thumbnailOf(project, cast) : null), [project, cast]);
  useEffect(() => dialogRef.current?.focus(), []);
  if (!project || !poster || !thumbnail) return null;

  const pick = (patch: ThumbnailPatch, label: string): void => commitThumbnail(patch, label);
  const lead = characterFor(cast, thumbnail.character);
  const rig = rigOf(lead);
  const members: ThumbnailMember[] = [...(thumbnail.ensemble ?? [])];
  const castLook = JSON.stringify(cast.characters.map((c) => [c.id, c.rig, c.palette]));
  const lookKey = (id: string, look: { pose?: string; expression?: string }) =>
    `${id}|${look.pose}|${look.expression}|${JSON.stringify(characterFor(cast, id).expressions)}`;
  const characterPaint = (id: string): TilePaint => (ctx, w, h, t) =>
    paintCharacterTile(ctx, w, h, performerOf(characterFor(cast, id), { pose: "rest", expression: "happy", move: "idle", t: t ?? CHARACTER_REST_T }), ENV);
  const lookPaint = (id: string, look: { pose: string; expression: string }, key: "pose" | "expression") => (option: string): TilePaint =>
    (ctx, w, h, t) => paintCharacterTile(ctx, w, h, performerOf(characterFor(cast, id), { ...look, move: "idle", [key]: option, t: t ?? CHARACTER_REST_T }), ENV);

  const castLead = (id: string): void => {
    pick({ character: id, ...(!wears(cast, id, thumbnail.expression) && { expression: null }) }, `Thumbnail lead: ${characterFor(cast, id).name}`);
  };
  const setMembers = (next: ThumbnailMember[], label: string): void => pick({ ensemble: membersOf(next) }, label);
  const castMember = (i: number, id: string): void => {
    const { expression, ...rest } = members[i];
    setMembers(members.map((m, j) => (j === i ? (wears(cast, id, expression) ? { ...m, character: id } : { ...rest, character: id }) : m)),
      `Thumbnail ensemble ${i + 1}: ${characterFor(cast, id).name}`);
  };
  const onStage = [lead.id, ...members.map((m) => characterFor(cast, m.character).id)];
  const newcomer = cast.characters.find((c) => !onStage.includes(c.id)) ?? cast.characters.find((c) => c.id !== lead.id) ?? lead;
  const words = (thumbnail.headline ?? "").trim().split(/\s+/).filter(Boolean).length;
  const [W, H] = POSTER_FORMATS[format];

  return (
    <div className="shot-layer" data-slot="thumbnail">
      <div className="shot" role="dialog" aria-modal="true" aria-label="Thumbnail" tabIndex={-1} ref={dialogRef}>
        <header className="shot-top">
          <div className="shot-title">
            <p className="eyebrow">Thumbnail</p>
            <h2>{project.title || project.name}</h2>
          </div>
          <span className="grow" />
          <div className="seg" role="group" aria-label="Thumbnail size">
            {FORMATS.map((f) => (
              <button key={f} type="button" className={f === format ? "on" : undefined} aria-pressed={f === format} onClick={() => setFormat(f)}>
                {f}
              </button>
            ))}
          </div>
          <button type="button" className="ghost" disabled={!project.thumbnail} title="Back to the defaults from the video's title and cast, and its footage cover."
            onClick={resetThumbnail}>
            Reset
          </button>
          <button type="button" className="icon" aria-label="Close" onClick={() => openThumbnail(false)}>×</button>
        </header>
        <div className="shot-grid">
          <div className="shot-stage thumb-stage">
            <div className="thumb-big" style={{ ["--ar" as string]: String(W / H) }}>
              <PosterPicture poster={poster} format={format} width={W} label={`${format} thumbnail`} />
            </div>
            <div className="thumb-small">
              <PosterPicture poster={poster} format={format} {...(format === "16:9" ? { width: 168, height: 94 } : { width: 94, height: 168 })} />
              <span>As YouTube shows it beside a video: {format === "16:9" ? "168 × 94" : "94 × 168"}.</span>
            </div>
          </div>
          <div className="recipe">
            <Field label="Title" stored={project.thumbnail?.headline ?? ""} placeholder={project.title || project.name} maxLength={60}
              onCommit={(v) => pick({ headline: v || null }, "Thumbnail title")}
              hint={<p className={`thumb-words${words > POSTER_WORDS * 2 ? " warn" : ""}`}>
                {words > POSTER_WORDS * 2 ? `${words} words: two lines of ${POSTER_WORDS} read best at thumbnail size.` : `Up to ${POSTER_WORDS} words a line, in big comic lettering.`}
              </p>} />
            <Field label="Eyebrow" stored={project.thumbnail?.eyebrow ?? ""} placeholder="None" maxLength={40}
              onCommit={(v) => pick({ eyebrow: v || null }, v ? "Thumbnail eyebrow" : "Hide thumbnail eyebrow")} />
            {cast.characters.length > 1 && (
              <Strip title="Lead" options={cast.characters.map((c) => ({ id: c.id, label: c.name }))} selected={lead.id} look={castLook}
                paint={characterPaint} onPick={castLead} />
            )}
            <Strip title="Lead expression" options={expressionOptions(cast, lead.id)} selected={poster.lead.expression} look={lookKey(lead.id, poster.lead)}
              paint={lookPaint(lead.id, poster.lead, "expression")} onPick={(id) => pick({ expression: id }, `Thumbnail lead expression: ${expressionName(id, lead)}`)} />
            <Strip title="Lead pose" options={options(rig.poses, POSE_NAMES)} selected={poster.lead.pose} look={lookKey(lead.id, poster.lead)}
              paint={lookPaint(lead.id, poster.lead, "pose")} onPick={(id) => pick({ pose: id }, `Thumbnail lead pose: ${nameOf(POSE_NAMES, id)}`)} />
            {members.map((member, i) => {
              const profile = characterFor(cast, member.character), look = poster.ensemble[i], n = i + 1, key = lookKey(profile.id, look);
              return (
                <div key={i} className="costar-set ensemble-set" data-slot={`thumbnail-ensemble-${n}`}>
                  <Strip title={`Behind ${n}`} options={cast.characters.map((c) => ({ id: c.id, label: c.name }))} selected={profile.id} look={castLook}
                    action={
                      <button type="button" className="ghost costar-remove" onClick={() => setMembers(members.filter((_, j) => j !== i), `Remove ${profile.name} from the thumbnail`)}>
                        Remove
                      </button>
                    }
                    paint={characterPaint} onPick={(id) => castMember(i, id)} />
                  <Strip title={`Behind ${n} expression`} options={expressionOptions(cast, profile.id)} selected={look.expression} look={key}
                    paint={lookPaint(profile.id, look, "expression")}
                    onPick={(id) => setMembers(members.map((m, j) => (j === i ? { ...m, expression: id } : m)), `Thumbnail ensemble ${n} expression: ${expressionName(id, profile)}`)} />
                  <Strip title={`Behind ${n} pose`} options={options(rigOf(profile).poses, POSE_NAMES)} selected={look.pose} look={key}
                    paint={lookPaint(profile.id, look, "pose")}
                    onPick={(id) => setMembers(members.map((m, j) => (j === i ? { ...m, pose: id } : m)), `Thumbnail ensemble ${n} pose: ${nameOf(POSE_NAMES, id)}`)} />
                  <Field label={`Behind ${n} label`} stored={member.badge?.text ?? ""} placeholder="None" maxLength={24}
                    onCommit={(v) => setMembers(members.map((m, j) => (j === i ? labelled(m, v ? { text: v, tone: member.badge?.tone ?? "accent" } : null) : m)),
                      v ? `Thumbnail ensemble ${n} label: ${v}` : `Remove the thumbnail ensemble ${n} label`)}
                    hint={member.badge && (
                      <div className="seg" role="group" aria-label={`Behind ${n} label colour`}>
                        {BADGE_TONES.map((tone) => (
                          <button key={tone} type="button" className={tone === member.badge?.tone ? "on" : undefined} aria-pressed={tone === member.badge?.tone}
                            onClick={() => setMembers(members.map((m, j) => (j === i && m.badge ? { ...m, badge: { ...m.badge, tone } } : m)), `Thumbnail ensemble ${n} label: ${TONE_NAMES[tone]}`)}>
                            {TONE_NAMES[tone]}
                          </button>
                        ))}
                      </div>
                    )} />
                </div>
              );
            })}
            {members.length < ENSEMBLE_MAX && (
              <div className="rrow ensemble-add">
                <button type="button" className="costar-add" title="Characters standing behind the lead: up to three."
                  onClick={() => setMembers([...members, { character: newcomer.id }], `Add to the thumbnail: ${newcomer.name}`)}>
                  + Add behind the lead
                </button>
              </div>
            )}
            <Strip title="Backdrop" options={BACKDROP_OPTIONS} selected={poster.backdrop} wide look="backdrop" paint={paintBackdrop}
              onPick={(id) => pick({ backdrop: id }, `Thumbnail backdrop: ${nameOf(BACKDROP_NAMES, id)}`)} />
          </div>
        </div>
      </div>
    </div>
  );
}
