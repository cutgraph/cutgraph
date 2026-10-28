// The Shot view directs one scene's picture:
// footage, a full-frame animation or a character cameo, which character performs it when the
// video can cast more than one, a co-star beside it and how the two interact, the look of the
// characters and the shot, and the eyebrow over its headline: the project's brand unless the
// shot names its own or hides it. "Show headline" turns the headline and eyebrow off altogether
// on the stage, a duo or an ensemble. A cameo springs in on its "Lands on" word. Every pick is
// one undo step, saved at once, and the Story player redraws the scene live. Under the stage: the
// critic line, the numbered takes, "Direct it" and the scene's code; the stage draws the player's captions.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  BACKDROPS, DIAGRAMS, INTERACTIONS, SHOT_MODES, isBoard, shotMode, shotTiming,
  type Interaction, type Shot as MotionShot, type ShotMode,
} from "motion-core";
import { persistCurrent } from "../../../persist";
import { captionPages } from "../../../preview/captionPages";
import { domEnv } from "../../../preview/SceneLayer";
import { resolveMedia } from "../../../preview/resolveMedia";
import { useSourceProxy } from "../../../preview/sourceProxy";
import { applyLocal, useProjectStore } from "../../../store/useProjectStore";
import { useDraft } from "../../common";
import { speechClipAt, timelineToSrc } from "../../../transcript/cutmap";
import type { Costar, Project } from "../../../types/project";
import { brandOf, castShot, characterFor, expressionName, isBuiltInExpression, performerOf, rigOf, type Cast } from "../../library/cast";
import { useVideoCast } from "../../library/castStore";
import type { SceneSpan } from "../model";
import { openShot, useStory } from "../storyStore";
import { useStoryModel } from "../useStoryModel";
import { BACKDROP_NAMES, DIAGRAM_NAMES, EXPRESSION_NAMES, INTERACTION_NAMES, MODE_NAMES, MOVE_NAMES, POSE_NAMES, nameOf } from "./names";
import ActingSet from "./ActingSet";
import { sceneCode } from "./code";
import { critic } from "./critic";
import DirectIt from "./DirectIt";
import { CHARACTER_REST_T, LOOK_REST_T, paintCharacterTile, paintDuoTile, paintLookTile } from "./paint";
import { Strip, type TileOption, type TilePaint } from "./Picker";
import ShotStage from "./ShotStage";
import StagingControls, { BoardLabels } from "./StagingControls";
import { useScreenAssets } from "./screenLibrary";
import { sceneSpec, withShot, type ShotPatch } from "./specs";
import { isTake, noteTake, takePatch, takesKey, useSceneTakes } from "./takes";
import "./shot.css";

const ENV = domEnv();
const FALLBACK_S = 3;

const MODE_HINT: Record<ShotMode, string> = {
  footage: "This scene shows its footage. Pick Animation or Cameo to direct a character.",
  animation: "A full-frame animated shot. The footage audio keeps playing underneath.",
  cameo: "The character springs into the lower corner over your footage, on its “Lands on” word.",
};

const options = (ids: readonly string[], names: Record<string, string>): TileOption[] =>
  ids.map((id) => ({ id, label: nameOf(names, id) }));
const BACKDROP_OPTIONS = options(BACKDROPS, BACKDROP_NAMES);
const DIAGRAM_OPTIONS = options(DIAGRAMS, DIAGRAM_NAMES);
const INTERACTION_OPTIONS = options(INTERACTIONS, INTERACTION_NAMES);

/** A profile's expressions as picker options: its rig's built-ins, then its own approved custom ones. */
function expressionOptions(cast: Cast, id: string | undefined): TileOption[] {
  const profile = characterFor(cast, id);
  return [
    ...options(Object.keys(rigOf(profile).expressions), EXPRESSION_NAMES),
    ...profile.expressions.filter((e) => !e.draft).map((e) => ({ id: e.id, label: e.name })),
  ];
}

/** Whether a profile can wear an expression id: a built-in of its rig or one of its own. */
function wears(cast: Cast, id: string, expression: string | undefined): boolean {
  const profile = characterFor(cast, id);
  return !expression || isBuiltInExpression(expression, rigOf(profile)) || profile.expressions.some((e) => e.id === expression);
}

/** Patch one scene's shot as one undo step and save it. */
export function commitShot(sceneId: string, patch: ShotPatch, label: string): void {
  const project = useProjectStore.getState().project;
  if (!project) return;
  const next = withShot(project, sceneId, patch);
  if (next === project) return;
  applyLocal(() => ({ project: next }), { origin: "human", label });
  void persistCurrent(project.name);
}

function footageStill(project: Project, span: SceneSpan): string | null {
  const at = span.tlStartF === null ? null : timelineToSrc(project, span.tlStartF);
  const clip = at ? speechClipAt(project, at.media, at.srcF) : null;
  const url = clip ? resolveMedia(clip.media) : null;
  return at && url ? `${url}#t=${(at.srcF / project.fps).toFixed(2)}` : null;
}

export default function ShotSheet() {
  const sceneId = useStory((s) => s.shotSceneId);
  const { project, words, spans, media, cut } = useStoryModel();
  useSourceProxy(project);
  const { cast } = useVideoCast();
  const [showCode, setShowCode] = useState(false);
  const [copied, setCopied] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const index = spans.findIndex((s) => s.scene.id === sceneId);
  const span = index >= 0 ? spans[index] : null;
  const scene = span?.scene ?? null;
  const stored = scene?.shot?.headline ?? "";
  const [draft, setDraft] = useDraft(stored, sceneId);
  const cancelBlur = useRef(false);
  const storedEyebrow = scene?.shot?.eyebrow;
  const [eyebrowDraft, setEyebrowDraft] = useDraft(storedEyebrow ?? "", sceneId);
  const cancelEyebrowBlur = useRef(false);

  const takes = useSceneTakes(takesKey(project?.name ?? "", sceneId ?? ""));
  const storedShot = scene?.shot;
  useEffect(() => {
    if (project && sceneId && scene) noteTake(takesKey(project.name, sceneId), storedShot);
  }, [project, sceneId, scene, storedShot]);

  useEffect(() => setCopied(false), [sceneId, showCode]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape") openShot(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const open = !!span;
  useEffect(() => {
    if (open) dialogRef.current?.focus();
  }, [open]);

  const screens = useScreenAssets(project);
  const spec = useMemo(
    () => (project && span ? sceneSpec(project, words, span, media, cast, screens) : null),
    [project, words, span, media, cast, screens],
  );
  const profile = characterFor(cast, scene?.shot?.character);
  const rig = rigOf(profile);
  const storedCostar = scene?.shot?.costar;
  const costarProfile = characterFor(cast, storedCostar?.character);
  const costarRig = rigOf(costarProfile);
  const leadExpressions = useMemo(() => expressionOptions(cast, profile.id), [cast, profile.id]);
  const costarExpressions = useMemo(() => expressionOptions(cast, costarProfile.id), [cast, costarProfile.id]);
  const captionsOn = project?.captions?.enabled === true;
  const pages = useMemo(
    () => (project && captionsOn && words.length ? captionPages(project, words, project.width, project.height) : null),
    [project, captionsOn, words],
  );

  if (!project || !span || !scene) return null;

  const mode = shotMode(scene.shot);
  const shot = spec?.shot ?? castShot(scene.shot, scene.role, scene.title, cast);
  const timing = spec?.timing ?? shotTiming(0, null, null, project.fps);
  const brand = brandOf(cast);
  const hideEyebrow = storedEyebrow === "";
  const eyebrow = hideEyebrow ? "" : eyebrowDraft.trim() || brand.name;
  const hideHeadline = !!scene.shot?.hideHeadline;
  const look = { mode, shot: { ...shot, headline: draft.trim() ? draft : scene.title, eyebrow }, timing, durS: spec ? spec.durF / project.fps : FALLBACK_S };
  const characterLook = `${profile.id}|${shot.pose}|${shot.expression}|${shot.move}|${JSON.stringify(profile.expressions)}`;
  const costar = storedCostar ? shot.costar : undefined;
  const costarLook = costar ? `${costarProfile.id}|${costar.pose}|${costar.expression}|${costar.move}|${JSON.stringify(costarProfile.expressions)}` : "";
  const shotLook = `${characterLook}|${costarLook}|${shot.interaction ?? ""}|${shot.backdrop}|${shot.diagram}|${shot.headline}|${JSON.stringify([shot.bubble, shot.costar?.bubble, shot.ensemble])}`;
  const kept: number[] = [];
  for (let i = span.startWord; i < span.endWord; i++) if (!cut.has(i)) kept.push(i);
  const cueWord = scene.shot?.cueWord;
  const cueAt = cueWord !== undefined && kept.includes(cueWord) ? cueWord : kept[0];

  const cueText = cueAt === undefined ? null : words[cueAt].w;
  const review = critic({ mode, layout: scene.shot?.layout, role: scene.role, shot, cueWord: cueText, profile });
  const screenName = screens.find((e) => e.asset.id === scene.shot?.screen?.asset)?.asset.name ?? null;
  const moreScreens = (scene.shot?.screen?.more ?? []).map((id) => screens.find((e) => e.asset.id === id)?.asset.name ?? "app screen");
  const code = showCode
    ? sceneCode({ n: index + 1, title: scene.title, role: scene.role, mode, stored: scene.shot, shot, cueWord: cueText, durS: look.durS, captions: captionsOn, screenName, moreScreens })
    : "";
  const copyCode = (): void => {
    void navigator.clipboard?.writeText(code).then(() => setCopied(true), () => setCopied(false));
  };

  const drafts = mode === "footage" ? [] : [profile, ...(mode === "animation" && costar ? [costarProfile] : [])].filter((p) => !p.approved);
  const pick = (patch: ShotPatch, label: string): void => commitShot(scene.id, patch, label);
  const leadPaint = (key: "expression" | "pose" | "move") => (id: string): TilePaint => (ctx, w, h, t) =>
    paintCharacterTile(ctx, w, h, performerOf(profile, { pose: shot.pose, expression: shot.expression, move: shot.move, [key]: id, t: t ?? CHARACTER_REST_T }), ENV);
  const characterPaint = (id: string): TilePaint => (ctx, w, h, t) =>
    paintCharacterTile(ctx, w, h, performerOf(characterFor(cast, id), { pose: "rest", expression: "happy", move: "idle", t: t ?? CHARACTER_REST_T }), ENV);
  const castCharacter = (id: string): void => {
    const next = characterFor(cast, id);
    pick({ character: id, ...(!wears(cast, id, scene.shot?.expression) && { expression: null }) }, `Character: ${next.name}`);
  };
  const other = cast.characters.find((c) => c.id !== profile.id) ?? profile;
  const addCostar = (): void => pick({ costar: { character: other.id } }, `Add co-star: ${other.name}`);
  const animateDuo = (): void => pick({ mode: "animation", costar: { character: other.id } }, `Animate with ${other.name}`);
  const costarAction = mode === "animation" && !costar && (
    <button type="button" className="costar-add" title={`A second character beside ${profile.name}: they can face each other, fist bump, high five or point, on the “Lands on” word.`}
      onClick={addCostar}>
      + Add co-star
    </button>
  );
  const pickCostar = (patch: Partial<Costar>, label: string): void => {
    if (storedCostar) pick({ costar: { ...storedCostar, ...patch } }, label);
  };
  const castCostar = (id: string): void => {
    if (!storedCostar) return;
    const { expression, ...rest } = storedCostar;
    pick({ costar: wears(cast, id, expression) ? { ...storedCostar, character: id } : { ...rest, character: id } }, `Co-star: ${characterFor(cast, id).name}`);
  };
  const costarPaint = (key: "expression" | "pose" | "move") => (id: string): TilePaint => (ctx, w, h, t) =>
    paintCharacterTile(ctx, w, h, performerOf(costarProfile, { pose: costar?.pose, expression: costar?.expression, move: costar?.move, [key]: id, t: t ?? CHARACTER_REST_T }), ENV);
  const interactionPaint = (id: string): TilePaint => (ctx, w, h, t) =>
    paintDuoTile(ctx, w, h, { ...shot, interaction: id as Interaction }, t, ENV);
  const lookPaint = (key: "backdrop" | "diagram") => (id: string): TilePaint => (ctx, w, h, t) =>
    paintLookTile(ctx, w, h, { ...shot, [key]: id } as MotionShot, t === null ? LOOK_REST_T : t % 3.2, ENV);
  const landsOn = (
    <div className="rrow">
      <h4>Lands on</h4>
      <div className="cuewords" role="group" aria-label="Lands on">
        {kept.map((i) => (
          <button key={i} type="button" className={i === cueAt ? "on" : undefined} aria-pressed={i === cueAt}
            onClick={() => pick({ cueWord: i }, `Lands on “${words[i].w}”`)}>
            {words[i].w}
          </button>
        ))}
      </div>
    </div>
  );
  const go = (to: number): void => {
    const next = spans[to];
    if (next) openShot(next.scene.id);
  };

  return (
    <div className="shot-layer" data-slot="shot">
      <div className="shot" role="dialog" aria-modal="true" aria-label="Shot" tabIndex={-1} ref={dialogRef}>
        <header className="shot-top">
          <button type="button" className="icon" aria-label="Previous scene" disabled={index === 0} onClick={() => go(index - 1)}>‹</button>
          <div className="shot-title">
            <p className="eyebrow">Shot · scene {index + 1} · {scene.role}</p>
            <h2>{scene.title}</h2>
          </div>
          <button type="button" className="icon" aria-label="Next scene" disabled={index === spans.length - 1} onClick={() => go(index + 1)}>›</button>
          {drafts.length > 0 && (
            <span className="shot-draft" title={`${drafts.map((p) => p.name).join(" and ")}: a draft look until approved in Library → Cast.`}>Draft</span>
          )}
          <span className="grow" />
          <button type="button" className="ghost" aria-expanded={showCode} onClick={() => setShowCode((v) => !v)}>
            {showCode ? "Hide code" : "View code"}
          </button>
          <button type="button" className="icon" aria-label="Close" onClick={() => openShot(null)}>×</button>
        </header>
        <div className="shot-grid">
          <div className="shot-stage">
            <ShotStage look={look} width={project.width} height={project.height} still={footageStill(project, span)} env={ENV}
              captions={pages && span.tlStartF !== null ? { pages, startS: span.tlStartF / project.fps } : null} />
            {review && (
              <p className={`critic ${review.ok ? "ok" : "warn"}`} role="status" aria-label="Critic">
                <b>{review.ok ? "✓ Critic" : "! Critic"}</b> {review.notes.join(" ")}
              </p>
            )}
            <div className="takes" role="group" aria-label="Takes">
              <span className="muted">Takes</span>
              {takes.map((take) => {
                const on = isTake(take, scene.shot);
                return (
                  <button key={take.n} type="button" className={on ? "on" : undefined} aria-pressed={on} aria-label={`Take ${take.n}`}
                    onClick={() => pick(takePatch(scene.shot, take), `Back to take ${take.n}`)}>
                    {take.n}
                  </button>
                );
              })}
            </div>
            <DirectIt video={project.name} sceneId={scene.id} n={index + 1} title={scene.title} mode={mode} />
            {showCode && (
              <div className="code-wrap">
                <div className="code-bar">
                  <button type="button" className="ghost code-copy" onClick={copyCode}>{copied ? "Copied" : "Copy"}</button>
                </div>
                <pre className="code" aria-label="Scene code">{code}</pre>
              </div>
            )}
          </div>
          <div className="recipe">
            <div className="rrow">
              <h4>Picture</h4>
              <div className="seg" role="group" aria-label="Picture">
                {SHOT_MODES.map((m) => (
                  <button key={m} type="button" className={m === mode ? "on" : undefined} aria-pressed={m === mode} onClick={() => pick({ mode: m }, `Picture: ${MODE_NAMES[m]}`)}>
                    {MODE_NAMES[m]}
                  </button>
                ))}
              </div>
              <p className="mode-hint">{MODE_HINT[mode]}</p>
              {mode === "footage" && cast.characters.length > 1 && (
                <div className="duo-hint">
                  <p className="mode-hint">Characters and “+ Add co-star” come with Animation: {profile.name} and {other.name} can share the scene.</p>
                  <button type="button" className="costar-add" onClick={animateDuo}>Animate with a co-star</button>
                </div>
              )}
            </div>
            {mode === "animation" && <StagingControls shot={scene.shot} workspaceId={project.workspaceId} onPick={pick} />}
            {mode !== "footage" && (
              <>
                {cast.characters.length > 1 && (
                  <Strip title="Character" action={costarAction || undefined} options={cast.characters.map((c) => ({ id: c.id, label: c.available === false ? `${c.name} · Unavailable` : c.name }))} selected={profile.id}
                    look={JSON.stringify(cast.characters.map((c) => [c.id, c.rig, c.palette]))} paint={characterPaint} onPick={castCharacter} />
                )}
                <Strip title="Expression" options={leadExpressions} selected={shot.expression} look={characterLook} paint={leadPaint("expression")}
                  onPick={(id) => pick({ expression: id }, `Expression: ${expressionName(id, profile)}`)} />
                <Strip title="Pose" options={options(rig.poses, POSE_NAMES)} selected={shot.pose} look={characterLook} paint={leadPaint("pose")}
                  onPick={(id) => pick({ pose: id }, `Pose: ${nameOf(POSE_NAMES, id)}`)} />
                <Strip title="Move" options={options(rig.moves, MOVE_NAMES)} selected={shot.move} look={characterLook} paint={leadPaint("move")}
                  onPick={(id) => pick({ move: id }, `Move: ${nameOf(MOVE_NAMES, id)}`)} />
              </>
            )}
            {mode === "animation" && costar && (
              <div className="costar-set" data-slot="costar">
                <Strip title="Co-star" options={cast.characters.map((c) => ({ id: c.id, label: c.available === false ? `${c.name} · Unavailable` : c.name }))} selected={costarProfile.id}
                  action={<button type="button" className="ghost costar-remove" onClick={() => pick({ costar: null, interaction: null }, "Remove co-star")}>Remove co-star</button>}
                  look={JSON.stringify(cast.characters.map((c) => [c.id, c.rig, c.palette]))} paint={characterPaint} onPick={castCostar} />
                <Strip title="Co-star expression" options={costarExpressions} selected={costar.expression} look={costarLook} paint={costarPaint("expression")}
                  onPick={(id) => pickCostar({ expression: id }, `Co-star expression: ${expressionName(id, costarProfile)}`)} />
                <Strip title="Co-star pose" options={options(costarRig.poses, POSE_NAMES)} selected={costar.pose} look={costarLook} paint={costarPaint("pose")}
                  onPick={(id) => pickCostar({ pose: id }, `Co-star pose: ${nameOf(POSE_NAMES, id)}`)} />
                <Strip title="Co-star move" options={options(costarRig.moves, MOVE_NAMES)} selected={costar.move} look={costarLook} paint={costarPaint("move")}
                  onPick={(id) => pickCostar({ move: id }, `Co-star move: ${nameOf(MOVE_NAMES, id)}`)} />
                <Strip title="Interaction" options={INTERACTION_OPTIONS} selected={shot.interaction ?? "none"} wide look={shotLook} paint={interactionPaint}
                  onPick={(id) => pick({ interaction: id === "none" ? null : (id as Interaction) }, `Interaction: ${nameOf(INTERACTION_NAMES, id)}`)} />
              </div>
            )}
            <ActingSet scene={scene} shot={shot} cast={cast} pick={pick} env={ENV} />
            {mode === "cameo" && landsOn}
            {mode === "animation" && (
              <>
                <Strip title="Backdrop" options={BACKDROP_OPTIONS} selected={shot.backdrop} wide look={shotLook} paint={lookPaint("backdrop")}
                  onPick={(id) => pick({ backdrop: id }, `Backdrop: ${nameOf(BACKDROP_NAMES, id)}`)} />
                <Strip title="Diagram" options={DIAGRAM_OPTIONS} selected={shot.diagram} wide look={shotLook} paint={lookPaint("diagram")}
                  onPick={(id) => pick({ diagram: id }, `Diagram: ${nameOf(DIAGRAM_NAMES, id)}`)} />
                {isBoard(shot.diagram) && <BoardLabels labels={scene.shot?.boardLabels ?? []} pick={pick} />}
                {landsOn}
                <div className="rrow eyebrow-row">
                  <div className="rhead">
                    <h4>Eyebrow</h4>
                    <button type="button" className={hideEyebrow ? "hide-toggle on" : "hide-toggle"} aria-pressed={hideEyebrow}
                      onClick={() => pick({ eyebrow: hideEyebrow ? null : "" }, hideEyebrow ? "Show eyebrow" : "Hide eyebrow")}>
                      Hide
                    </button>
                  </div>
                  <input
                    value={hideEyebrow ? "" : eyebrowDraft}
                    placeholder={hideEyebrow ? "Hidden" : brand.name}
                    aria-label="Eyebrow"
                    maxLength={40}
                    disabled={hideEyebrow}
                    style={{ color: brand.accent }}
                    onChange={(e) => setEyebrowDraft(e.target.value)}
                    onBlur={() => {
                      if (cancelEyebrowBlur.current) cancelEyebrowBlur.current = false;
                      else pick({ eyebrow: eyebrowDraft.trim() || null }, "Edit eyebrow");
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") e.currentTarget.blur();
                      else if (e.key === "Escape") {
                        e.stopPropagation();
                        cancelEyebrowBlur.current = true;
                        setEyebrowDraft(storedEyebrow ?? "");
                        e.currentTarget.blur();
                      }
                    }}
                  />
                </div>
                <div className="rrow">
                  <div className="rhead">
                    <h4>Headline</h4>
                    <button type="button" className={hideHeadline ? "hide-toggle" : "hide-toggle on"} aria-pressed={!hideHeadline}
                      title="Also hides the eyebrow, on the stage, a duo or an ensemble."
                      onClick={() => pick({ hideHeadline: hideHeadline ? null : true }, hideHeadline ? "Show headline" : "Hide headline")}>
                      Show headline
                    </button>
                  </div>
                  <input
                    value={draft}
                    placeholder={scene.title}
                    aria-label="Headline"
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => {
                      if (cancelBlur.current) cancelBlur.current = false;
                      else pick({ headline: draft.trim() || null }, "Edit headline");
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") e.currentTarget.blur();
                      else if (e.key === "Escape") {
                        e.stopPropagation();
                        cancelBlur.current = true;
                        setDraft(stored);
                        e.currentTarget.blur();
                      }
                    }}
                  />
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
