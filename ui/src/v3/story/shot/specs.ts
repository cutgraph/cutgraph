// The scene render spec rule (the animation contract) and the shot patches the Shot view makes.
// The export computes the same rule in Python; the two must stay identical.
import { shotMode, shotTiming } from "motion-core";
import type { TranscriptWord } from "../../../api";
import type { SceneRenderSpec } from "../../../preview/SceneLayer";
import { resolveTimed, srcToTimeline, wordSrcFrame } from "../../../transcript/cutmap";
import type { Project, Scene, Shot } from "../../../types/project";
import { castShot, NO_CAST, type Cast } from "../../library/cast";
import type { LibraryAsset } from "../drawer/library";
import type { SceneSpan } from "../model";
import { withScreenImage } from "./screenLibrary";

/**
 * One scene's spec whatever its mode, or null when the scene is fully cut. A custom expression is
 * resolved from `cast` and a screen's image from `assets` (the video's libraries, screenLibrary.ts).
 * `eff` is resolveTimed(words), passed in when building many specs at once.
 */
export function sceneSpec(
  project: Project,
  words: TranscriptWord[],
  span: SceneSpan,
  media: string | null,
  cast: Cast = NO_CAST,
  assets: readonly LibraryAsset[] = [],
  eff: number[] = resolveTimed(words),
): SceneRenderSpec | null {
  if (span.tlStartF === null || span.durF === 0) return null;
  const keptF = (i: number): number | null => {
    const t = eff[i];
    return media && t !== undefined && t >= 0 ? srcToTimeline(project, media, wordSrcFrame(words[t], project.fps)) : null;
  };
  let firstWordF: number | null = null;
  for (let i = span.startWord; i < span.endWord && firstWordF === null; i++) firstWordF = keptF(i);
  const { scene } = span;
  const cue = scene.shot?.cueWord;
  const cueWordF = cue !== undefined && cue >= span.startWord && cue < span.endWord ? keptF(cue) : null;
  const startF = span.tlStartF;
  return {
    sceneId: scene.id,
    mode: shotMode(scene.shot),
    shot: withScreenImage(castShot(scene.shot, scene.role, scene.title, cast), assets),
    timing: shotTiming(startF, firstWordF, cueWordF, project.fps, span.durF),
    startF,
    durF: span.durF,
  };
}

/** Every scene the preview draws a picture for, in story order: footage and fully cut scenes are left out. */
export function animatedSceneSpecs(
  project: Project,
  words: TranscriptWord[],
  spans: SceneSpan[],
  media: string | null,
  cast: Cast = NO_CAST,
  assets: readonly LibraryAsset[] = [],
): SceneRenderSpec[] {
  const eff = resolveTimed(words);
  return spans.flatMap((span) => {
    if (shotMode(span.scene.shot) === "footage") return [];
    const spec = sceneSpec(project, words, span, media, cast, assets, eff);
    return spec ? [spec] : [];
  });
}

/** A change to one scene's shot; `null` (or an empty headline) removes the field, and an empty eyebrow hides it. */
export type ShotPatch = { [K in keyof Shot]?: Shot[K] | null };

/**
 * The project with one scene's shot patched, or the same project when nothing changes. Footage is the
 * default mode, so it is stored by removing `mode` (the rest of the look is kept for switching back),
 * and a shot left empty is removed from the scene.
 */
export function withShot(project: Project, sceneId: string, patch: ShotPatch): Project {
  const scenes = project.scenes ?? [];
  const at = scenes.findIndex((s) => s.id === sceneId);
  if (at < 0) return project;
  const scene = scenes[at];
  const before: Record<string, unknown> = { ...scene.shot };
  const shot = { ...before };
  for (const [key, value] of Object.entries(patch)) {
    if (value === null || value === undefined || (value === "" && key !== "eyebrow") || (key === "mode" && value === "footage")) delete shot[key];
    else shot[key] = value;
  }
  const keys = Object.keys(shot);
  if (keys.length === Object.keys(before).length && keys.every((k) => JSON.stringify(shot[k]) === JSON.stringify(before[k]))) return project;
  const next: Scene = { ...scene, shot: shot as Shot };
  if (!keys.length) delete next.shot;
  return { ...project, scenes: scenes.map((s, i) => (i === at ? next : s)) };
}
